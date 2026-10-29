"""One lease and retry history per immutable attachment version/configuration."""
import asyncio


async def process(repo,job):
    from .media import extract
    args=(job['object_id'],job['binding_id'],job['revision'],job['config'])
    with repo.cursor() as c:
        c.execute('SELECT public.kb_file_claim(%s,%s,%s,%s) AS token',args);token=c.fetchone()['token']
    if not token:return None
    try:
        result=await extract(repo,*args[:3])
        if result is None:raise ValueError('MODEL_NOT_CONFIGURED')
        with repo.cursor() as c:
            c.execute('SELECT public.kb_file_finish(%s,%s,%s,%s,%s) AS done',(*args,token));return c.fetchone()['done']
    except asyncio.CancelledError:raise
    except Exception as error:
        with repo.cursor() as c:c.execute('SELECT public.kb_file_finish(%s,%s,%s,%s,%s,%s)',(*args,token,str(error) if str(error)=='MODEL_NOT_CONFIGURED' else type(error).__name__))
        raise
