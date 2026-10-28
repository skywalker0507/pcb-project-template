import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import source_references as references
from electrical_backend import sha256


class SourceReferenceTests(unittest.TestCase):
    def fixture(self,root):
        (root/'config').mkdir();(root/'references').mkdir()
        source=root/'references/vendor.pdf';source.write_bytes(b'original-reference')
        payload=dict(schema=1,files=[dict(path='references/vendor.pdf',sha256=sha256(source),scope='Existing source geometry only')])
        (root/references.MANIFEST).write_text(json.dumps(payload))
        return source,payload

    def test_optional_absence_has_no_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:self.assertIsNone(references.inputs(Path(tmp)))

    def test_durable_reference_survives_cache_removal_and_is_hash_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();source,_=self.fixture(root)
            cache=root/'tmp';cache.mkdir();(cache/'vendor.pdf').write_bytes(source.read_bytes())
            expected=references.inputs(root);(cache/'vendor.pdf').unlink();cache.rmdir()
            self.assertEqual(references.inputs(root),expected)
            self.assertEqual(references.require({'source_references':expected},root),expected)
            source.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'hash mismatch'):references.inputs(root)

    def test_disposable_and_escaping_locations_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            for location in ['tmp/a.pdf','build/release/a.pdf','.git/a.pdf','../a.pdf']:
                with self.subTest(location=location):
                    with self.assertRaises(ValueError):references.durable_files(root,[root/location])

    @unittest.skipUnless(shutil.which('git'),'Git is required')
    def test_ignored_reference_cannot_become_a_required_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();self.fixture(root)
            subprocess.run(['git','init','--quiet',str(root)],check=True,capture_output=True)
            (root/'.gitignore').write_text('references/\n')
            with self.assertRaisesRegex(ValueError,'Git-ignored'):references.inputs(root)

    def test_missing_duplicate_hashless_or_scopeless_entries_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();_,payload=self.fixture(root)
            entries=[{'path':'references/missing.pdf','sha256':'X','scope':'test'},
                     {'path':'references/vendor.pdf','scope':'test'},
                     {'path':'references/vendor.pdf','sha256':payload['files'][0]['sha256']}]
            for entry in entries:
                (root/references.MANIFEST).write_text(json.dumps(dict(schema=1,files=[entry])))
                with self.assertRaises(ValueError):references.inputs(root)
            (root/references.MANIFEST).write_text(json.dumps(dict(schema=1,files=payload['files']*2)))
            with self.assertRaisesRegex(ValueError,'Duplicate'):references.inputs(root)

    def test_freeze_requires_current_manifest_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();self.fixture(root)
            binding=references.inputs(root)
            with self.assertRaisesRegex(ValueError,'changed'):references.require({},root)
            (root/references.MANIFEST).write_text((root/references.MANIFEST).read_text()+' ')
            with self.assertRaisesRegex(ValueError,'changed'):references.require({'source_references':binding},root)
