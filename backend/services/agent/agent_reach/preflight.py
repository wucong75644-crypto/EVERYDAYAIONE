"""Read-only deployment checks. Never tests login or performs platform writes.

Run from backend: python -m services.agent.agent_reach.preflight [--offline]
Output contains labels and booleans, never config values or exception text.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile

from .contracts import ReachError
from .login_providers import google_config,xhs_slots

PLATFORMS={'web','github','rss','youtube','bilibili','twitter','reddit','xiaohongshu','exa'}


def runtime_requirements(path):
    result={}
    for line in path.read_text().splitlines():
        line=line.strip()
        if not line or line.startswith('#'): continue
        if ' @ git+' in line:
            name,source=line.split(' @ ',1); result[name]={'commit':source.rsplit('@',1)[1]}
        elif '==' in line:
            name,version=line.split('==',1); result[name]={'version':version}
        else: raise ValueError('Invalid locked requirement')
    return result


def inspect_runtime(bin_dir, lock_path):
    root=Path(bin_dir)
    names=['python','agent-reach','twitter','bili','rdt','yt-dlp']
    if any(not (root/name).is_file() or not os.access(root/name,os.X_OK) for name in names):
        return False,'独立运行环境不完整，请执行部署目录中的安装脚本'
    expected=runtime_requirements(lock_path)
    with tempfile.TemporaryDirectory(prefix='reach-probe-') as home:
        try:
            result=subprocess.run([str(root/'python'),'-I',str(Path(__file__).with_name('runtime_probe.py'))],
                input=lock_path.read_text(),capture_output=True,text=True,timeout=15,
                cwd=home,env={'HOME':home,'PATH':str(root)+':/usr/bin:/bin','LANG':'C.UTF-8'})
            if result.returncode or len(result.stdout)>64000:
                return False,'运行环境元数据检查失败'
            actual=json.loads(result.stdout)
        except (OSError,subprocess.TimeoutExpired,ValueError):
            return False,'无法读取独立运行环境版本'
    if any(not all(actual.get(name,{}).get(key)==value for key,value in wanted.items()) for name,wanted in expected.items()):
        return False,'独立运行环境版本与固定依赖不一致，请安装匹配版本'
    return True,'运行环境与固定依赖一致；未验证平台登录'


def configuration_checks(settings, platforms, *, lock_path=None):
    checks=[]
    def add(name,ok,message): checks.append({'name':name,'state':'ready' if ok else 'blocked','message':message})
    unknown=platforms-PLATFORMS
    add('platforms',bool(platforms) and not unknown,'查询渠道名称有效' if platforms and not unknown else '查询渠道为空或存在不支持的名称')
    checks.append({'name':'feature','state':'ready' if settings.agent_reach_enabled else 'disabled',
        'message':'总开关已开启' if settings.agent_reach_enabled else '总开关关闭，配置检查不会自动开启'})
    if platforms & {'youtube','bilibili','twitter','reddit'}:
        try:
            ok,message=inspect_runtime(settings.agent_reach_bin_dir,lock_path or Path(__file__).parents[3]/'requirements-agent-reach.freeze.txt')
        except (OSError,ValueError): ok,message=False,'固定依赖清单不可用'
        add('runtime',ok,message)
    if 'youtube' in platforms:
        add('youtube.javascript',(Path(settings.agent_reach_bin_dir)/'node').is_file(),'字幕需独立运行环境中的Node；存在不代表字幕实际可用')
        try: google_config(settings); ok=True
        except ReachError: ok=False
        add('youtube.oauth',ok,'官方OAuth配置字段完整，仍需同意屏幕与真实授权验收' if ok else '缺少YouTube官方应用配置或HTTPS回调地址无效')
    if 'exa' in platforms:
        add('exa.api_key',bool(settings.agent_reach_exa_api_key),'Exa密钥已填写，额度和调用仍需验收' if settings.agent_reach_exa_api_key else '尚未配置Exa服务密钥')
    if 'xiaohongshu' in platforms:
        try:
            groups=json.loads(settings.agent_reach_xhs_slots_json)
            count=sum(len(xhs_slots(settings,org)) for org in groups)
            # Legacy configured connections remain operable without new slots.
            legacy=json.loads(settings.agent_reach_xhs_origins_json)
            from .xhs_adapter import XHSAdapter
            from uuid import UUID
            for cid in legacy:
                UUID(cid)
                XHSAdapter(json.dumps(legacy), {'id':cid,'account_id':'probe','secret':{'service_token':'probe'}})
            ok=count>0 or bool(legacy)
        except (ReachError,ValueError,TypeError): ok=False
        add('xiaohongshu.slots',ok,'专用服务映射已填写；服务、共享素材目录与登录仍需验收' if ok else '缺少组织专用扫码服务或映射冲突')
    writes={value.strip() for value in settings.agent_reach_write_actions.split(',') if value.strip()}
    from .write_adapters import check_write
    for entry in sorted(writes):
        try:
            from types import SimpleNamespace
            platform,action=entry.split(':')
            check_write(SimpleNamespace(platform=platform,action=action))
            ok=platform in platforms
        except (ValueError,ReachError): ok=False
        add('write_action',ok,'写动作与查询渠道配置匹配' if ok else '写动作未支持或对应渠道未启用')
    if writes:
        path=Path(settings.agent_reach_media_staging_dir)
        parent=path if path.exists() else path.parent
        ok=(not path.is_symlink() and parent.is_dir() and os.access(parent,os.W_OK)
            and (not path.exists() or (path.stat().st_uid==os.getuid() and not path.stat().st_mode&0o077)))
        add('media.directory',ok,'媒体目录权限可用；本检查未创建目录' if ok else '媒体目录或父目录权限不满足服务账号独占要求')
    try:
        from services.configuration.envelope import LocalKEKProvider
        LocalKEKProvider.from_environment(); ok=True
    except (ValueError,RuntimeError): ok=False
    add('credential.encryption',ok,'凭据加密配置可加载' if ok else '组织账号加密密钥未配置或无效')
    return checks


async def infrastructure_checks(settings):
    checks=[]
    try:
        import psycopg
        def schema():
            with psycopg.connect(settings.database_url,connect_timeout=5) as db:
                columns=db.execute("""SELECT table_name,column_name FROM information_schema.columns
                    WHERE table_schema='public' AND table_name IN
                    ('reach_connections','reach_connection_grants','reach_operations')""").fetchall()
                actual=set(columns)
                required={('reach_connections','login_state'),('reach_connections','checked_at'),
                    ('reach_connections','secret_envelope'),('reach_connection_grants','can_write'),('reach_operations','payload_digest')}
                index=db.execute("SELECT 1 FROM pg_indexes WHERE schemaname='public' AND indexname='reach_active_platform_account'").fetchone()
                return required.issubset(actual) and bool(index)
        ready=await asyncio.to_thread(schema)
        checks.append({'name':'database.schema','state':'ready' if ready else 'blocked','message':
            '新表和登录字段存在' if ready else '数据库缺少291/292迁移所需表、字段或索引'})
    except Exception:
        checks.append({'name':'database.schema','state':'blocked','message':'数据库连接或只读结构检查失败'})
    try:
        from redis.asyncio import Redis
        client=Redis.from_url(settings.redis_url,socket_connect_timeout=5,socket_timeout=5)
        try: ready=await client.ping()
        finally: await client.aclose()
        checks.append({'name':'redis','state':'ready' if ready else 'blocked','message':'登录会话存储可达'})
    except Exception:
        checks.append({'name':'redis','state':'blocked','message':'登录会话存储不可达'})
    return checks


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline',action='store_true',help='只检查本地配置，不连接数据库/Redis')
    parser.add_argument('--platforms',help='检查计划使用的逗号分隔渠道，默认取实际配置')
    args=parser.parse_args()
    try:
        from core.config import get_settings
        settings=get_settings()
        platforms={value.strip() for value in (args.platforms or settings.agent_reach_platforms).split(',') if value.strip()}
        checks=configuration_checks(settings,platforms)
        if args.offline:
            checks.append({'name':'infrastructure','state':'unchecked','message':'离线模式未检查数据库与Redis'})
        else: checks+=await infrastructure_checks(settings)
        report={'configuration_ready':not any(item['state']=='blocked' for item in checks),
            'platform_acceptance':'not_verified','checks':checks}
    except Exception:
        report={'configuration_ready':False,'platform_acceptance':'not_verified','checks':[
            {'name':'settings','state':'blocked','message':'应用配置无法加载，请检查必需字段；未输出配置值'}]}
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['configuration_ready'] else 1


if __name__=='__main__':
    raise SystemExit(asyncio.run(main()))
