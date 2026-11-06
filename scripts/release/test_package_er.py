"""Offline release-package regressions; no credentials or native compiler required."""
import json
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import package_er as package


class PackageTests(unittest.TestCase):
    def test_macos_lipo_has_input_before_verify_arch(self):
        binary = Path('/tmp/package with spaces/bin/er')
        dependencies = f'{binary}:\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n\t/System/Library/Frameworks/Security.framework/Versions/A/Security (compatibility version 1.0.0)\n'
        with patch.object(package, 'run') as run, patch.object(package.subprocess, 'check_output', return_value=dependencies):
            package.verify_architecture(binary, 'aarch64-apple-darwin')
        run.assert_called_once_with(['lipo', binary, '-verify_arch', 'arm64'])

    def test_macos_rejects_unbundled_dylibs(self):
        for dependency in ('/opt/homebrew/lib/libx.dylib', '/usr/local/lib/libx.dylib', '@rpath/libx.dylib'):
            with self.subTest(dependency=dependency), patch.object(package, 'run'), patch.object(package.subprocess, 'check_output', return_value=f'er:\n\t{dependency} (compatibility version 1.0.0)\n'):
                with self.assertRaisesRegex(AssertionError, 'Unbundled'):
                    package.verify_architecture(Path('/tmp/er'), 'aarch64-apple-darwin')

    def test_linux_checks_machine_and_static_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'er'
            for target, machine in (('x86_64-unknown-linux-musl', 62), ('aarch64-unknown-linux-musl', 183)):
                header = bytearray(64)
                header[:6] = b'\x7fELF\x02\x01'
                struct.pack_into('<H', header, 18, machine)
                binary.write_bytes(header)
                with patch.object(package.subprocess, 'check_output', side_effect=['LOAD', 'No dynamic section']):
                    package.verify_architecture(binary, target)
                for headers, dynamic in (('INTERP', ''), ('LOAD', '(NEEDED) libc.so.6')):
                    with patch.object(package.subprocess, 'check_output', side_effect=[headers, dynamic]):
                        with self.assertRaisesRegex(AssertionError, 'Not static'):
                            package.verify_architecture(binary, target)

    def test_windows_rejects_wrong_machine(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'er.exe'
            header = bytearray(70)
            header[:2] = b'MZ'
            struct.pack_into('<I', header, 60, 64)
            header[64:68] = b'PE\0\0'
            struct.pack_into('<H', header, 68, 0x8664)
            binary.write_bytes(header)
            package.verify_architecture(binary, 'x86_64-pc-windows-msvc')
            struct.pack_into('<H', header, 68, 0xAA64)
            binary.write_bytes(header)
            with self.assertRaises(AssertionError):
                package.verify_architecture(binary, 'x86_64-pc-windows-msvc')

    def test_tar_roundtrip_tests_extracted_bytes_and_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / 'er-1.2.3-linux-x86_64-musl'
            (staging / 'bin').mkdir(parents=True)
            binary = staging / 'bin/er'
            binary.write_bytes(b'fixture bytes')
            binary.chmod(0o755)
            archive = root / (staging.name + '.tar.gz')
            package.archive_package(staging, archive)
            def verify(extracted, target, version, commit):
                self.assertNotEqual(extracted, staging)
                self.assertEqual((extracted / 'bin/er').read_bytes(), b'fixture bytes')
                self.assertTrue((extracted / 'bin/er').stat().st_mode & 0o111)
                self.assertEqual((target, version, commit), ('x86_64-unknown-linux-musl', '1.2.3', 'a' * 40))
            with patch.object(package, 'verify_package', side_effect=verify) as verifier:
                package.verify_archive(archive, 'x86_64-unknown-linux-musl', '1.2.3', 'a' * 40)
                verifier.assert_called_once()
            digest, filename = archive.with_name(archive.name + '.sha256').read_text().split()
            self.assertEqual((digest, filename), (package.digest(archive), archive.name))

    def test_zip_rejects_paths_outside_package(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'bad.zip'
            with zipfile.ZipFile(archive, 'w') as output:
                output.writestr('../outside', 'no')
            with self.assertRaises(AssertionError):
                package.verify_archive(archive, 'x86_64-pc-windows-msvc', '1.2.3', 'a' * 40)

    def test_provenance_rejected_before_executing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'codex-package.json').write_text(json.dumps({'target': 'aarch64-apple-darwin', 'version': '1.2.3', 'entrypoint': 'bin/er'}))
            (root / 'BUILD-INFO.json').write_text(json.dumps({'target': 'aarch64-apple-darwin', 'version': '1.2.3', 'sourceCommit': 'b' * 40}))
            with patch.object(package, 'run') as run, self.assertRaises(AssertionError):
                package.verify_package(root, 'aarch64-apple-darwin', '1.2.3', 'a' * 40)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
