from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import electrical_backend as backend


class FootprintSourceIdentityTests(unittest.TestCase):
    def test_plain_and_qualified_identities_work_in_nested_libraries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            library=root/'parts/deep/Part.pretty'
            library.mkdir(parents=True)
            (library/'One.kicad_mod').write_text('(footprint "Part:One")')
            (library/'Two.kicad_mod').write_text('(footprint "Two")')
            (library/'Legacy.kicad_mod').write_text('(module Legacy)')
            backend.validate_footprint_identities(root)

    def test_stale_copied_namespace_is_rejected_before_compilation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            source=root/'src'
            library=source/'parts/NewPart'
            library.mkdir(parents=True)
            (source/'main.ato').write_text('module App:\n    pass\n')
            footprint=library/'NewPart.kicad_mod'
            footprint.write_text('(footprint "OldPart:NewPart")')
            before=footprint.read_bytes()
            (root/'driver.py').write_text('')
            (root/'ato.yaml').write_text('builds:\n  default:\n    entry: src/main.ato:App\n')
            config=dict(driver='driver.py',source_root='src',entry='main.ato:App',status='primary',platform='test')
            with mock.patch.object(backend,'ROOT',root):
                with self.assertRaisesRegex(SystemExit,'identity mismatch.*OldPart:NewPart'):
                    backend.validate_backend({},'atopile',config)
            self.assertEqual(footprint.read_bytes(),before)

    def test_wrong_internal_item_and_unreadable_resource_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            library=root/'Part'
            library.mkdir()
            footprint=library/'Part.kicad_mod'
            footprint.write_text('(footprint "Wrong")')
            with self.assertRaisesRegex(ValueError,'expected Part:Part'):
                backend.validate_footprint_identities(root)
            footprint.write_text('broken file')
            with self.assertRaisesRegex(ValueError,'no readable identity'):
                backend.validate_footprint_identities(root)

    def test_escaped_quoted_item_is_compared_after_decoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            library=root/'Part'
            library.mkdir()
            (library/'Part Name.kicad_mod').write_text('(footprint "Part:Part\\u0020Name")')
            backend.validate_footprint_identities(root)


if __name__=='__main__':
    unittest.main()
