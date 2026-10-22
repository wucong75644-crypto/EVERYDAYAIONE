"""The historical command now dispatches to one local controlled executor."""
import sys
import unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ci'))
import task_delivery as task

class LocalDeliveryTests(unittest.TestCase):
    def test_deploy_is_local_and_scoped(self):
        with patch.object(sys,'argv',['task_delivery.py','deploy','--authorization','提交部署','--message','fix: x','--file','x.py']),patch.object(task.subprocess,'run') as run,patch.object(task,'submit') as remote:
            task.main()
        self.assertEqual(run.call_args.args[0],['bash','deploy/release.sh','--message','fix: x','--file','x.py'])
        remote.assert_not_called()
    def test_accept_is_local(self):
        with patch.object(sys,'argv',['task_delivery.py','accept','--authorization','清理工作树']),patch.object(task.subprocess,'run') as run:
            task.main()
        self.assertEqual(run.call_args.args[0],['bash','deploy/release.sh','--accept-and-close'])
    def test_unauthorized_deploy_does_not_run(self):
        with patch.object(sys,'argv',['task_delivery.py','deploy']),patch.object(task.subprocess,'run') as run:
            with self.assertRaises(RuntimeError):task.main()
        run.assert_not_called()
    def test_ci_id_cannot_bypass_local_checks(self):
        with patch.object(sys,'argv',['task_delivery.py','deploy','--authorization','提交部署','--check-run','123']),patch.object(task.subprocess,'run') as run:
            with self.assertRaises(RuntimeError):task.main()
        run.assert_not_called()

if __name__=='__main__':unittest.main()
