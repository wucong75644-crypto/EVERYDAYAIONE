"""Real temporary Git integration; no production SSH, data or GitHub writes."""
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from scripts.testing import test_release_coordination as fixtures

SOURCE=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(SOURCE/'scripts/ci'))
import task_delivery as task
import delivery


class TaskDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.ReleaseCoordinationTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.root=self.f.task('b')
        self.branch=self.f.git('branch','--show-current',cwd=self.root).stdout.strip()
        self.addCleanup(patch.stopall)
        patch.object(task,'ROOT',self.root).start()
        patch.object(delivery,'ROOT',self.root).start()
        patch.object(delivery,'git',lambda *args,cwd=None:self.f.git(*args,cwd=cwd or self.root).stdout.strip()).start()
    def commit(self,path,name,text):
        (path/name).write_text(text)
        self.f.git('add',name,cwd=path);self.f.git('commit','-m','test: '+name,cwd=path)
        branch=self.f.git('branch','--show-current',cwd=path).stdout.strip()
        self.f.git('push','origin',branch,cwd=path)
        return self.f.git('rev-parse','HEAD',cwd=path).stdout.strip()
    def advance_main(self,name='a.txt',text='A accepted\n'):
        return self.commit(self.f.repository,name,text)
    def test_prepare_keeps_b_and_latest_a_then_checks_final_sha(self):
        old=self.commit(self.root,'b.txt','B work\n');self.advance_main()
        branch,new=task.prepare()
        self.assertNotEqual(new,old);self.assertEqual(branch,self.branch)
        self.assertEqual((self.root/'a.txt').read_text(),'A accepted\n')
        self.assertEqual((self.root/'b.txt').read_text(),'B work\n')
        self.assertEqual(self.f.git('ls-remote','origin','refs/heads/'+branch,cwd=self.root).stdout.split()[0],new)
        with patch.object(task,'prepare',return_value=(branch,new)),patch.object(task,'ensure_checks',side_effect=RuntimeError('new SHA failed')) as checks,patch.object(delivery,'request') as request:
            with self.assertRaisesRegex(RuntimeError,'new SHA failed'):task.submit('production','提交部署')
            checks.assert_called_once_with(new);request.assert_not_called()
    def test_conflict_is_preserved_without_push_or_check_and_can_be_resolved(self):
        old=self.commit(self.root,'same.txt','B change\n');self.advance_main('same.txt','A change\n')
        with self.assertRaisesRegex(RuntimeError,'CONFLICT'):task.prepare()
        self.assertTrue(self.f.git('ls-files','-u',cwd=self.root).stdout)
        self.assertEqual(self.f.git('ls-remote','origin','refs/heads/'+self.branch,cwd=self.root).stdout.split()[0],old)
        # Codex resolves both intended edits; no ours/theirs overwrite policy.
        (self.root/'same.txt').write_text('A change\nB change\n')
        self.f.git('add','same.txt',cwd=self.root);self.f.git('commit','-m','test: preserve A and B',cwd=self.root)
        _,new=task.prepare();self.assertNotEqual(new,old)
        self.assertEqual((self.root/'same.txt').read_text(),'A change\nB change\n')
    def test_uncommitted_edits_are_not_touched(self):
        (self.root/'uncommitted.txt').write_text('preserve')
        with self.assertRaisesRegex(RuntimeError,'Commit scoped'):task.prepare()
        self.assertEqual((self.root/'uncommitted.txt').read_text(),'preserve')
    def test_no_instruction_never_prepares_or_requests(self):
        for mode in ('production','accept'):
            with patch.object(task,'prepare') as prepare,patch.object(delivery,'request') as request,self.assertRaises(RuntimeError):
                task.submit(mode,None)
            prepare.assert_not_called();request.assert_not_called()
    def test_failed_acceptance_cannot_remove_original_worktree(self):
        sha=self.f.git('rev-parse','HEAD',cwd=self.root).stdout.strip()
        run={'head_branch':'delivery-accept/'+sha+'/123/'+self.branch.removeprefix('codex/task/')+'/012345abcdef',
             'head_sha':sha,'status':'completed','conclusion':'failure','event':'push',
             'path':'.github/workflows/delivery.yml','actor':{'login':task.REPOSITORY.split('/')[0]}}
        with patch.object(delivery,'api',return_value=run),self.assertRaises(RuntimeError):task.finalize_acceptance(1,'清理工作树')
        self.assertTrue(self.root.exists())
    def successful_run(self,sha):
        return {'head_branch':'delivery-accept/'+sha+'/123/'+self.branch.removeprefix('codex/task/')+'/012345abcdef',
             'head_sha':sha,'status':'completed','conclusion':'success','event':'push',
             'path':'.github/workflows/delivery.yml','actor':{'login':task.REPOSITORY.split('/')[0]}}
    def test_main_changes_after_acceptance_preserve_original_task(self):
        sha=self.commit(self.root,'b.txt','accepted B\n')
        self.f.git('merge','--no-ff','--no-edit',self.branch)
        self.f.git('push','origin','main')
        self.advance_main('later.txt','another accepted change\n')
        with patch.object(delivery,'api',side_effect=[self.successful_run(sha),{'jobs':[{'name':'acceptance','conclusion':'success'}]}]),patch.object(delivery,'verify_checks'),self.assertRaisesRegex(RuntimeError,'Main changed'):
            task.finalize_acceptance(1,'清理工作树')
        self.assertTrue(self.root.exists())
    def test_local_task_changed_after_acceptance_preserve_original_task(self):
        old=self.commit(self.root,'b.txt','accepted B\n')
        self.commit(self.root,'new.txt','new development\n')
        with patch.object(delivery,'api',return_value=self.successful_run(old)),self.assertRaisesRegex(RuntimeError,'No successful exact acceptance'):
            task.finalize_acceptance(1,'清理工作树')
        self.assertTrue(self.root.exists());self.assertEqual((self.root/'new.txt').read_text(),'new development\n')
    def test_successful_exact_acceptance_closes_only_original_task(self):
        sha=self.commit(self.root,'b.txt','accepted B\n')
        # Simulate GitHub acceptance's no-ff main merge in local bare fixture.
        self.f.git('merge','--no-ff','--no-edit',self.branch)
        self.f.git('push','origin','main')
        run={'head_branch':'delivery-accept/'+sha+'/123/'+self.branch.removeprefix('codex/task/')+'/012345abcdef',
             'head_sha':sha,'status':'completed','conclusion':'success','event':'push',
             'path':'.github/workflows/delivery.yml','actor':{'login':task.REPOSITORY.split('/')[0]}}
        jobs={'jobs':[{'name':'acceptance','conclusion':'success'}]}
        with patch.object(delivery,'api',side_effect=[run,jobs]),patch.object(delivery,'verify_checks'):
            task.finalize_acceptance(1,'清理工作树')
        self.assertFalse(self.root.exists());self.assertTrue(self.f.repository.exists())
        self.assertTrue(self.f.git('ls-remote','origin','refs/heads/'+self.branch).stdout)



class CheckReuseTests(unittest.TestCase):
    sha = 'a' * 40

    def run_info(self, number=1, **changes):
        return {'id': number, 'path': '.github/workflows/pre-release-checks.yml',
                'head_sha': self.sha, 'head_branch': 'codex/task/a',
                'status': 'completed', 'conclusion': 'success', **changes}

    def test_exact_success_reused_without_request(self):
        with patch.object(task, 'runs', return_value=[self.run_info()]), \
             patch.object(delivery, 'verify_checks', return_value={'mode': 'scoped'}) as verify, \
             patch.object(task.subprocess, 'run') as request:
            self.assertEqual(task.ensure_checks(self.sha), 1)
            verify.assert_called_once_with(task.REPOSITORY, 1, self.sha, return_plan=True)
            request.assert_not_called()

    def test_inflight_waited_without_duplicate_and_invalid_proof_blocks(self):
        with patch.object(task, 'runs', return_value=[self.run_info(status='in_progress')]), \
             patch.object(task, 'wait_run') as wait, \
             patch.object(delivery, 'verify_checks', side_effect=RuntimeError('scope mismatch')), \
             patch.object(task.subprocess, 'run') as request:
            with self.assertRaisesRegex(RuntimeError, 'scope mismatch'):
                task.ensure_checks(self.sha)
            wait.assert_called_once_with(1, self.sha)
            request.assert_not_called()

    def test_new_sha_cannot_reuse_old_green(self):
        old = self.run_info(head_sha='b' * 40)
        new = self.run_info(number=2)
        with patch.object(task, 'runs', side_effect=[[old], [old, new]]), \
             patch.object(task, 'wait_run') as wait, \
             patch.object(delivery, 'verify_checks', return_value={'mode': 'scoped'}), \
             patch.object(task.subprocess, 'run') as request:
            self.assertEqual(task.ensure_checks(self.sha), 2)
            wait.assert_called_once_with(2, self.sha)
            self.assertEqual(request.call_count, 1)

    def test_failed_latest_never_falls_back_to_old_success(self):
        with patch.object(task, 'runs', return_value=[self.run_info(), self.run_info(2, conclusion='failure')]), \
             patch.object(task, 'wait_run', side_effect=RuntimeError('failed')) as wait, \
             patch.object(task.subprocess, 'run') as request:
            with self.assertRaisesRegex(RuntimeError, 'failed'):
                task.ensure_checks(self.sha)
            wait.assert_called_once_with(2, self.sha)
            request.assert_not_called()

    def test_full_request_cannot_reuse_scoped_run(self):
        new = self.run_info(2, head_branch='preflight/full/task')
        with patch.object(task, 'runs', side_effect=[[self.run_info()], [new]]), \
             patch.object(task, 'wait_run'), \
             patch.object(delivery, 'verify_checks', side_effect=[{'frontend': 'related', 'backend': 'none'}, {'frontend': 'full', 'backend': 'full'}]), \
             patch.object(task.subprocess, 'run') as request:
            self.assertEqual(task.ensure_checks(self.sha, check_mode='full'), 2)
            self.assertEqual(request.call_args.args[0][-2:], ['--mode', 'full'])

    def test_full_reuses_scoped_run_that_already_executed_both_full_checks(self):
        with patch.object(task, 'runs', return_value=[self.run_info()]), \
             patch.object(delivery, 'verify_checks', return_value={'mode': 'scoped', 'frontend': 'full', 'backend': 'full'}), \
             patch.object(task.subprocess, 'run') as request:
            self.assertEqual(task.ensure_checks(self.sha, check_mode='full'), 1)
            request.assert_not_called()

    def test_document_only_never_requests_production_or_closes_task(self):
        with patch.object(task, 'prepare', return_value=('codex/task/a', self.sha)), \
             patch.object(task, 'ensure_checks', return_value=1), \
             patch.object(delivery, 'verify_checks', return_value={'deploy': False, 'mode': 'scoped'}), \
             patch.object(delivery, 'request') as request, \
             patch.object(task, 'finalize_acceptance') as close:
            result = task.submit('production', '提交部署')
            self.assertEqual(result['deployment'], 'skipped')
            request.assert_not_called(); close.assert_not_called()

if __name__=='__main__':unittest.main()
