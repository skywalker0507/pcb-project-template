import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import contextlib
import io
import subprocess

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import build_electrical as builder
import electrical_build_identity as identity
from electrical_backend import sha256


def manifest():
    core=dict(schema=1,backend='atopile',build='default',project='test',
        backend_config=dict(driver='scripts/driver.py',entry='main.ato:App',source_root='src',status='primary',runtime='wsl-ato',platform='wsl-native',
                            source_preflight=dict(script='check.py',inputs=['rules.json'])),
        contract=dict(source_sha256='S'*64,config_sha256='C'*64),
        producer_dependencies=[dict(path=p,sha256='A'*64) for p in ['scripts/driver.py','scripts/electrical_backend.py',
            'scripts/validate_atopile_source.py','ato.yaml','config/project-initialization.json','scripts/build_electrical.py',
            'scripts/check_electrical_candidate_integrity.py','rules.json']],
        toolchain=dict(identity=dict(version='0.15.8'),receipt_sha256='T'*64))
    return {**core,'fingerprint':identity.fingerprint(core)}


class CompilationReuseTests(unittest.TestCase):
    def current(self,old):
        core=copy.deepcopy(old);core.pop('fingerprint');core['schema']=2
        core['compiler_input']=identity.compiler_inputs(core)
        return {**core,'fingerprint':identity.fingerprint(core)}

    def test_validation_changes_preserve_compiler_identity_but_not_full_validation_identity(self):
        old=manifest();new=self.current(old)
        new['backend_config']['source_preflight']['inputs']=['references/rules.json']
        new['contract']['config_sha256']='D'*64
        for entry in new['producer_dependencies']:
            if entry['path'] in ['scripts/build_electrical.py','scripts/check_electrical_candidate_integrity.py','rules.json']:
                entry['sha256']='B'*64
        new['compiler_input']=identity.compiler_inputs(new)
        new['fingerprint']=identity.fingerprint({k:v for k,v in new.items() if k!='fingerprint'})
        self.assertEqual(identity.recorded_compiler_inputs(old),identity.recorded_compiler_inputs(new))
        self.assertNotEqual(old['fingerprint'],new['fingerprint'])

    def test_source_entry_driver_toolchain_project_and_initialization_changes_require_compile(self):
        old=manifest();expected=identity.recorded_compiler_inputs(old)
        for kind in ['source','entry','driver','toolchain','project','initialization','build']:
            new=copy.deepcopy(old)
            if kind=='source':new['contract']['source_sha256']='Z'*64
            elif kind=='entry':new['backend_config']['entry']='other.ato:App'
            elif kind=='driver':new['producer_dependencies'][0]['sha256']='Z'*64
            elif kind=='toolchain':new['toolchain']['identity']['version']='new'
            elif kind=='project':new['project']='other'
            elif kind=='build':new['build']='other'
            else:next(e for e in new['producer_dependencies'] if e['path']=='config/project-initialization.json')['sha256']='Z'*64
            new['fingerprint']=identity.fingerprint({k:v for k,v in new.items() if k!='fingerprint'})
            with self.subTest(kind=kind):self.assertNotEqual(expected,identity.recorded_compiler_inputs(new))

    def test_missing_inconsistent_or_tampered_manifests_never_qualify(self):
        bad=manifest();bad['project']='tampered'
        cases=[bad,{'schema':9},None]
        missing=manifest();missing['producer_dependencies'].pop(0)
        missing['fingerprint']=identity.fingerprint({k:v for k,v in missing.items() if k!='fingerprint'});cases.append(missing)
        inconsistent=self.current(manifest());inconsistent['compiler_input']['fingerprint']='F'*64
        inconsistent['fingerprint']=identity.fingerprint({k:v for k,v in inconsistent.items() if k!='fingerprint'});cases.append(inconsistent)
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):identity.recorded_compiler_inputs(value)

    def fixture(self,root):
        evidence=root/'build/03-electrical/evidence/old';evidence.mkdir(parents=True)
        raw=root/'build/03-electrical/tmp/old/raw.kicad_pcb';raw.parent.mkdir(parents=True);raw.write_text('raw')
        audit=evidence/'integrity.json';audit.write_text(json.dumps(dict(kind='electrical_candidate_integrity',success=True,
            read_only_inputs_unchanged=True,candidate_board_sha256=sha256(raw),source_sha256='S'*64)))
        old=manifest();payload=dict(success=True,backend='atopile',compiler_version='0.15.8',build_input=old,
            candidate_board=str(raw),candidate_board_sha256=sha256(raw),compiler_candidate_board=str(raw),
            compiler_candidate_board_sha256=sha256(raw),contract=old['contract'],candidate_integrity=dict(receipt=str(audit),sha256=sha256(audit)))
        receipt=evidence/'electrical-build.json';receipt.write_text(json.dumps(payload))
        return receipt,raw,self.current(old)

    def test_legacy_candidate_is_a_compile_cache_only_until_current_gates_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();receipt,raw,current=self.fixture(root)
            with mock.patch.object(builder,'ROOT',root),mock.patch.object(builder,'relative',side_effect=lambda p:p.relative_to(root).as_posix()):
                self.assertIsNone(builder.find_matching_build(current))
                result=builder.find_matching_compilation(current)
                self.assertEqual(result['compiler_candidate_sha256'],sha256(raw))
                raw.write_text('tampered')
                self.assertIsNone(builder.find_matching_compilation(current))

    def test_compiler_origin_is_content_bound_and_flattened(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();receipt,raw,current=self.fixture(root)
            build=dict(backend='atopile',compiler_version='0.15.8',build_input=current,compiler_candidate_board_sha256=sha256(raw),
                compiler_reuse=dict(origin_receipt=str(receipt),origin_receipt_sha256=sha256(receipt),
                                    compiler_candidate_sha256=sha256(raw),compiler_input_fingerprint=current['compiler_input']['fingerprint']))
            self.assertEqual(identity.require_compiler_reuse(build,root),receipt)
            receipt.write_text('{}')
            with self.assertRaisesRegex(ValueError,'changed'):identity.require_compiler_reuse(build,root)

    def test_plan_requests_revalidation_without_requiring_a_compile(self):
        output=__import__('io').StringIO()
        with mock.patch.object(sys,'argv',['build','--plan']),mock.patch.object(builder,'read_config',return_value={}), \
             mock.patch.object(builder,'selected_backend',return_value=('test',{})),mock.patch.object(builder,'validate_backend',return_value={}), \
             mock.patch.object(builder.source_preflight,'run',return_value=None),mock.patch.object(builder.source_references,'inputs',return_value=None), \
             mock.patch.object(builder,'production_toolchain',return_value=None),mock.patch.object(builder,'build_input_manifest',return_value={'fingerprint':'F'}), \
             mock.patch.object(builder,'find_matching_build',return_value=None),mock.patch.object(builder,'find_matching_compilation',return_value={'receipt':'old'}), \
             __import__('contextlib').redirect_stdout(output):
            self.assertEqual(builder.main(),0)
        plan=json.loads(output.getvalue())
        self.assertFalse(plan['build_required']);self.assertTrue(plan['validation_required'])
        self.assertEqual(plan['next_action'],'revalidate_electrical_candidate')
        self.assertIsNotNone(plan['next_command'])

    def test_failed_fresh_gate_stops_cached_revalidation_without_starting_compiler(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();origin,raw,_=self.fixture(root)
            payload=json.loads(origin.read_text());payload['backend']='test'
            old=payload['build_input'];old['backend']='test'
            old['fingerprint']=identity.fingerprint({k:v for k,v in old.items() if k!='fingerprint'})
            origin.write_text(json.dumps(payload))
            current=self.current(old);backend=current['backend_config']
            output=root/'build/03-electrical/tmp/new';receipt=root/'build/03-electrical/evidence/new/electrical-build.json'
            cache=dict(receipt=str(origin),receipt_sha256=sha256(origin),origin_receipt=str(origin),origin_receipt_sha256=sha256(origin),
                       compiler_candidate=str(raw),compiler_candidate_sha256=sha256(raw),compiler_input_fingerprint=current['compiler_input']['fingerprint'])
            calls=[]
            def reject(command,**kwargs):
                calls.append(command)
                self.assertTrue(str(command[1]).endswith('check_electrical_candidate_integrity.py'))
                audit=Path(command[command.index('--output')+1]);audit.write_text(json.dumps(dict(success=False,failed_refs=['U1'])))
                return subprocess.CompletedProcess(command,1,'rejected')
            status=dict(source_sha256='S'*64,config_sha256='C'*64)
            stdout=io.StringIO()
            with mock.patch.object(builder,'ROOT',root),mock.patch.object(builder,'relative',side_effect=lambda p:p.relative_to(root).as_posix()), \
                 mock.patch.object(builder,'workspace_path',side_effect=lambda value,label:root/value), \
                 mock.patch.object(builder,'read_config',return_value={}),mock.patch.object(builder,'selected_backend',return_value=('test',backend)), \
                 mock.patch.object(builder,'validate_backend',return_value=status),mock.patch.object(builder,'production_toolchain',return_value=None), \
                 mock.patch.object(builder,'build_input_manifest',return_value=current),mock.patch.object(builder,'find_matching_build',return_value=None), \
                 mock.patch.object(builder,'find_matching_compilation',return_value=cache),mock.patch.object(builder.source_preflight,'run',return_value=None), \
                 mock.patch.object(builder.subprocess,'run',side_effect=reject), \
                 mock.patch.object(sys,'argv',['build','--output',str(output),'--receipt',str(receipt)]),contextlib.redirect_stdout(stdout):
                self.assertEqual(builder.main(),1)
            failed=json.loads(receipt.read_text())
            self.assertFalse(failed['success']);self.assertFalse(failed['compiler_invoked'])
            self.assertEqual(failed['failure']['class'],'electrical_candidate_integrity_failed')
            self.assertEqual(len(calls),1)
            self.assertEqual(raw.read_text(),'raw')
