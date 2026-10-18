import importlib.util
import hashlib
import re
import subprocess
import io
from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('gateway',ROOT/'scripts/ci/ssh_gateway.py')
gateway=importlib.util.module_from_spec(spec);spec.loader.exec_module(gateway)
spec=importlib.util.spec_from_file_location('policy',ROOT/'scripts/ci/build_ssh_policy.py')
policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)

class GatewayTests(unittest.TestCase):
    def test_probe_only(self):
        self.assertEqual(gateway.classify('everydayai-ci-probe-v1')[0],'probe')
    def test_arbitrary_commands_denied(self):
        for command in ('','id','bash -c id','cat /root/.ssh/id_rsa','everydayai-ci-probe-v1; id','scp -t /tmp/a','sudo bash -c id'):
            with self.subTest(command=command),self.assertRaises(ValueError):gateway.classify(command)
    def test_release_commands(self):
        self.assertEqual(gateway.classify('bash')[0],'deployment')
        self.assertEqual(gateway.classify('sudo bash -s')[0],'storage')
        self.assertEqual(gateway.classify('bash -s -- /var/www/everydayai read 20261018-123')[0],'coordination')
        self.assertEqual(gateway.classify('bash -s -- backend/migrations/123_test.sql')[0],'migrations')
    def test_argument_injection_denied(self):
        for command in ('bash -s -- /tmp/x read token','bash -s -- /var/www/everydayai acquire token extra','bash -s -- /var/www/everydayai record t '+ 'a'*40 +' preview "x;id"','bash -s -- backend/migrations/rollback/123_a.sql','bash -s -- backend/migrations/123_a.sql;id'):
            with self.subTest(command=command),self.assertRaises(ValueError):gateway.classify(command)
    def test_rsync_receiver(self):
        with patch.object(Path,'resolve',lambda p:p):
            for path in ('frontend/','backend/','backend/../deploy/'):
                kind,args=gateway.classify('rsync --server -vlogDtprze.iLsfxCIvu --delete . /var/www/everydayai/'+path)
                self.assertEqual(kind,'rsync');self.assertIn('--filter=-r .env*',args)
    def test_rsync_escape_denied(self):
        for command in ('rsync --server --sender -vlogDtprze.iLsfxCIvu . /var/www/everydayai/backend/','rsync --server -vlogDtprze.iLsfxCIvu . /root/','rsync --server -vlogDtprze.iLsfxCIvu --files-from=/etc/shadow . /var/www/everydayai/backend/'):
            with self.subTest(command=command),self.assertRaises(ValueError):gateway.classify(command)
        with patch.object(Path,'is_symlink',return_value=True),self.assertRaises(ValueError):
            gateway.classify('rsync --server -vlogDtprze.iLsfxCIvu . /var/www/everydayai/backend/')
    def test_real_heredoc_bytes_match_policy(self):
        catalog=policy.build()['scripts']
        for filename,delimiter,kinds in [('deploy/deploy.sh','ENDSSH',('deployment','migrations')),('deploy/release-coordination.sh','REMOTE_STATE',('coordination',))]:
            source=(ROOT/filename).read_text()
            pattern=r"<< ?'"+delimiter+r"'[^\n]*\n(.*?)^"+delimiter+r"$"
            matches=re.findall(pattern,source,re.M|re.S)
            accepted=0
            for body in matches:
                actual=subprocess.check_output(['bash','-c',"cat <<'"+delimiter+"'\n"+body+delimiter+"\n"])
                hashed=hashlib.sha256(actual).hexdigest()
                if any(hashed in catalog[k] for k in kinds):accepted+=1
            self.assertEqual(accepted,4 if delimiter=='ENDSSH' else 1)
    def test_unknown_stdin_never_runs(self):
        p=Mock()
        p.stat.return_value=SimpleNamespace(st_uid=0,st_mode=0o600)
        p.read_text.return_value=__import__('json').dumps(policy.build())
        with patch.object(gateway,'POLICY',p),patch.dict(gateway.os.environ,{'SSH_ORIGINAL_COMMAND':'bash'}),patch.object(gateway.sys,'stdin',SimpleNamespace(buffer=io.BytesIO(b'echo changed-script\n'))),patch.object(gateway.subprocess,'run') as run:
            with self.assertRaises(ValueError):gateway.main()
            run.assert_not_called()
    def test_policy_contains_only_expected_scripts(self):
        p=policy.build()
        self.assertEqual({k:len(v) for k,v in p['scripts'].items()},{'deployment':3,'migrations':1,'storage':1,'python':3,'coordination':1})
        for values in p['scripts'].values():
            for value in values:self.assertRegex(value,r'^[0-9a-f]{64}$')

if __name__=='__main__':unittest.main()
