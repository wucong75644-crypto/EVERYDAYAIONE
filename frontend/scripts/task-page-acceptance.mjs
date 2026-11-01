/** Local native MessageItem acceptance. All API responses are fixtures.
 * No provider, backend, production credential, or installation is used.
 */
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { mkdir, writeFile } from 'node:fs/promises';
const root=fileURLToPath(new URL('..',import.meta.url));
const require=createRequire(join(root,'package.json'));
const {createServer}=await import(require.resolve('vite'));
const runtime=process.env.TASK_PAGE_PLAYWRIGHT_MODULE;
const {chromium}=await import(runtime ? pathToFileURL(runtime).href : 'playwright');
const port=Number(process.env.TASK_PAGE_ACCEPTANCE_PORT || 5179);
if(!Number.isInteger(port)||port<1024||port>65535)throw new Error('Invalid local test port');
const base='http://127.0.0.1:'+port;
const output=process.env.TASK_PAGE_ACCEPTANCE_OUTPUT || join(tmpdir(),'everydayai-task-page-'+Date.now());
await mkdir(output,{recursive:true});
process.env.VITE_API_BASE_URL='/api';
let mode='paused',revision=1;const calls=[];
const server=await createServer({root,configFile:root+'/vite.config.ts',server:{host:'127.0.0.1',port,strictPort:true},plugins:[{
 name:'local-task-acceptance',configureServer(server){server.middlewares.use(async(req,res,next)=>{
 const url=new URL(req.url,base);
 if(url.pathname==='/__task_page_acceptance') {res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml(url.pathname,'<html><body><div id="root"></div><script type="module" src="/tests/browser/task-progress.fixture.jsx"></script></body></html>'));return;}
 if(url.pathname==='/__local_state'){if(url.searchParams.has('mode')){mode=url.searchParams.get('mode');revision++;}res.setHeader('Content-Type','application/json');res.end(JSON.stringify({mode,calls}));return;}
 if(!url.pathname.startsWith('/api/'))return next();
 calls.push({method:req.method,path:url.pathname});res.setHeader('Content-Type','application/json');
 if(url.pathname.endsWith('/execution')){
 if(mode==='denied'){res.statusCode=404;res.end('{}');return;}
 res.end(JSON.stringify({root_task_id:'11111111-1111-4111-8111-111111111111',revision,status:mode,
 completed_steps:1,total_steps:2,completed_requirements:1,total_requirements:2,pending_results:0,unconfirmed_results:1,
 steps:[{id:'plan',title:'原规划与已有成图',status:'completed'},{id:'erp',title:'核实原ERP写入回执',status:'needs_verification'}]}));return;}
 if(url.pathname.includes('/cancel-by-message/')){mode='cancelled';revision++;res.end(JSON.stringify({success:true,cancelled_count:1}));return;}
 if(url.pathname.includes('/pause-by-message/')){mode='paused';revision++;res.end(JSON.stringify({success:true,paused_count:1,requested_count:0}));return;}
 if(url.pathname.includes('/resume-by-message/')){mode='active';revision++;res.end(JSON.stringify({success:true,resumed_count:1,tasks:[{outcome:'enqueued',logical_task_id:'11111111-1111-4111-8111-111111111111',conversation_id:'local-conversation',assistant_message_id:'new-message',client_task_id:'new-window'}]}));return;}
 res.statusCode=409;res.end(JSON.stringify({code:'LOCAL_ACCEPTANCE_UNEXPECTED_REQUEST'}));
 });}
}]});await server.listen();
let browser;
try {
browser=await chromium.launch({headless:true,...(process.env.TASK_PAGE_BROWSER_EXECUTABLE ? {executablePath:process.env.TASK_PAGE_BROWSER_EXECUTABLE} : {})});
const page=await browser.newPage({viewport:{width:1280,height:1000}});const errors=[],blocked=[];
page.on('pageerror',error=>errors.push(String(error)));
await page.route('**/*',route=>{const url=new URL(route.request().url());if(url.origin===base||url.protocol==='data:')return route.continue();blocked.push(url.origin);return route.abort();});
const assert=(ok,label)=>{if(!ok)throw new Error(label);};
 const previous=await (await page.request.get(base+'/__local_state?mode=paused')).json();
 const start=previous.calls.length;
 await page.goto(base+'/__task_page_acceptance');
 await page.getByRole('button',{name:'继续任务',exact:true}).waitFor();
 await page.getByRole('button',{name:'继续任务',exact:true}).click();
 await page.getByText('继续请求已受理，保留原成果等待接续。',{exact:true}).waitFor();
 await page.getByRole('button',{name:'暂停任务',exact:true}).waitFor();
 assert((await page.locator('body').innerText()).includes('原规划完整原文'),'lost original plan');
 assert(await page.locator('img[src^="data:image"]').count()===2,'lost original images');
 await page.screenshot({path:join(output,'desktop.png'),fullPage:true});
 await page.getByRole('button',{name:'暂停任务',exact:true}).click();
 await page.getByRole('button',{name:'继续任务',exact:true}).waitFor();
 await page.setViewportSize({width:390,height:1100});
 await page.screenshot({path:join(output,'mobile.png'),fullPage:true});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'mobile horizontal overflow');
 await page.request.get(base+'/__local_state?mode=completed');
 await page.getByRole('button',{name:'刷新进度',exact:true}).click();
 await page.getByRole('status').filter({hasText:'全部完成'}).waitFor();
 await page.clock.install();
 // Install before refreshing so the next closed-root timer is controlled.
 await Promise.all([page.waitForResponse(response=>response.url().includes('/execution')),page.getByRole('button',{name:'刷新进度',exact:true}).click()]);
 await page.clock.runFor(300);
 await page.getByRole('status').filter({hasText:'全部完成'}).waitFor();
 await page.request.get(base+'/__local_state?mode=active');
 const updated=page.waitForResponse(response=>response.url().includes('/execution'));
 await page.clock.fastForward(61_000);
 await updated;
 await page.clock.runFor(300);
 await page.getByRole('status').filter({hasText:'正在推进'}).waitFor();
 await page.request.get(base+'/__local_state?mode=denied');
 await page.getByRole('button',{name:'刷新进度',exact:true}).click();
 await page.getByRole('status').filter({hasText:'暂时无法读取任务进度'}).waitFor();
 assert(await page.locator('img[src^="data:image"]').count()===2,'denial lost published images');
 assert(await page.getByRole('button',{name:'暂停任务',exact:true}).count()===0,'denied controls still visible');
 await page.request.get(base+'/__local_state?mode=active');
 await page.getByRole('button',{name:'刷新进度',exact:true}).click();
 await page.getByRole('button',{name:'停止任务',exact:true}).waitFor();
 await page.getByRole('button',{name:'停止任务',exact:true}).click();
 await page.getByRole('status').filter({hasText:'已取消'}).waitFor();
 assert(await page.locator('img[src^="data:image"]').count()===2,'cancel lost published images');
 const facts=await (await page.request.get(base+'/__local_state')).json();
 facts.calls=facts.calls.slice(start);
 assert(facts.calls.filter(c=>c.path.includes('/resume-by-message/')).length===1,'duplicate resume');
 assert(facts.calls.filter(c=>c.path.includes('/pause-by-message/')).length===1,'duplicate pause');
 assert(facts.calls.filter(c=>c.path.includes('/cancel-by-message/')).length===1,'duplicate stop');
 assert(!facts.calls.some(c=>/generate|regenerate|replay|messages.*POST/.test(c.path)),'unexpected generation');
 assert(errors.length===0,errors.join('\n'));
 assert(blocked.length===0,'unexpected external request');
 const evidence={passed:true,checks:['actual MessageItem render','logical resume accepted once','old plan and two image resources retained','pause and stop via original endpoints','mobile width','closed root follow-up refresh','revocation clears progress controls'],facts,blockedOrigins:blocked,pageErrors:errors,screenshots:[join(output,'desktop.png'),join(output,'mobile.png')]};
 await writeFile(join(output,'evidence.json'),JSON.stringify(evidence,null,2));console.log(JSON.stringify({passed:true,requests:facts.calls.length,blockedOrigins:blocked.length,pageErrors:errors.length}));
} finally {await browser?.close();await server.close();}
