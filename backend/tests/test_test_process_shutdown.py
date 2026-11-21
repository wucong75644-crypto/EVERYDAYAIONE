"""The test runner must finalize orphan generators before native clock teardown."""
import os
from pathlib import Path
import subprocess
import sys


def test_native_clock_orphan_generator_exits_cleanly(tmp_path):
    test = tmp_path / 'test_orphan.py'
    test.write_text('''import gc, time, time_machine

def test_orphan():
    with time_machine.travel('2026-04-10'):
        pass
    gc.disable()
    def leave_cycle(clock):
        box = []
        def work():
            try:
                yield box
            finally:
                clock()
        generator = work()
        box.append(generator)
        next(generator)
    leave_cycle(time.time)
''')
    env = os.environ.copy()
    backend = str(Path(__file__).parents[1])
    env['PYTHONPATH'] = os.pathsep.join(filter(None, (backend, env.get('PYTHONPATH'))))
    result = subprocess.run([sys.executable, '-m', 'pytest', '-p', 'tests.conftest',
                             str(test), '-q'], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.returncode, result.stdout[-2000:], result.stderr[-2000:])


def test_chat_history_stream_fixtures_exit_without_live_metrics_pool(tmp_path):
    backend = Path(__file__).parents[1]
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join(filter(None, (str(backend), env.get('PYTHONPATH'))))
    env['DATABASE_URL'] = 'postgresql://unused'
    env['SUPABASE_DB_URL'] = 'postgresql://unused'
    result = subprocess.run([sys.executable, '-m', 'pytest',
                             str(backend / 'tests/test_chat_context.py'),
                             str(backend / 'tests/test_time_context.py'), '-q'],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.returncode, result.stdout[-2000:], result.stderr[-2000:])


def test_function_loop_closes_its_real_knowledge_pool(tmp_path):
    test = tmp_path / 'test_owned_pool.py'
    test.write_text('''import asyncio, pytest, time_machine
from services import knowledge_config as cfg

@pytest.mark.asyncio
async def test_create():
    with time_machine.travel('2026-04-10'):
        pass
    cfg._pg_pool = None
    cfg._kb_available = None
    assert await cfg._get_pg_pool() is not None
    await asyncio.sleep(0)

@pytest.mark.asyncio
async def test_previous_loop_released_its_pool():
    assert cfg._pg_pool is None
''')
    backend = str(Path(__file__).parents[1])
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join(filter(None, (backend, env.get('PYTHONPATH'))))
    env['DATABASE_URL'] = 'postgresql://unused'
    env['SUPABASE_DB_URL'] = 'postgresql://unused'
    result = subprocess.run([sys.executable, '-m', 'pytest', '-p', 'tests.conftest',
                             str(test), '-q', '-o', 'asyncio_mode=auto'],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.returncode, result.stdout[-2000:], result.stderr[-2000:])
    assert 'Task was destroyed' not in result.stderr
