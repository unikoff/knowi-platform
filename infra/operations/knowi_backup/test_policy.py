import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).parent))
from common import checked_id, removable, atomic_json


class Retention(unittest.TestCase):
    def points(self,n):
        return [{'id':f'knowi-core-202611{10+i:02d}T000000Z-abcdef00','server_restore_verified':True,'offsite_verified':True} for i in range(n)]

    def test_newest_locally_verified_copy_rotates_without_offsite_ack(self):
        items=self.points(5);items[-1]['offsite_verified']=False
        self.assertEqual(removable(items),[items[0]])

    def test_fifth_verified_copy_rotates_only_oldest_verified(self):
        items=self.points(5)
        self.assertEqual(removable(items),[items[0]])
        items[0]['offsite_verified']=False
        self.assertEqual(removable(items),[items[0]])

    def test_offline_computer_does_not_stop_retention(self):
        items=self.points(7)
        for item in items:item['offsite_verified']=False
        self.assertEqual(removable(items),items[:3])

    def test_unverified_new_snapshot_cannot_authorize_deletion(self):
        items=self.points(5);items[-1]['server_restore_verified']=False
        with self.assertRaises(RuntimeError):removable(items)

    def archive_fixture(self,directory,n):
        from common import sha
        for item in self.points(n):
            archive=directory/(item['id']+'.cms');archive.write_bytes(item['id'].encode())
            item.update(bytes=archive.stat().st_size,sha256=sha(archive),offsite_verified=False)
            (directory/(item['id']+'.json')).write_text(json.dumps(item))

    def test_real_archive_rotation_keeps_latest_four_with_pc_offline(self):
        import common
        from unittest.mock import patch
        base=Path(__file__).resolve().parents[3]/'output/knowi-backup-tests';base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as root:
            directory=Path(root);self.assertEqual(directory.resolve().parent,base.resolve())
            self.archive_fixture(directory,7)
            unrelated=directory/'manual-backup.cms';unrelated.write_bytes(b'keep')
            with patch.object(common,'ARCHIVES',directory):
                self.assertEqual(common.prune(),[x['id'] for x in self.points(7)[:3]])
                self.assertEqual([x['id'] for x in common.records()],[x['id'] for x in self.points(7)[-4:]])
            self.assertEqual(len(list(directory.glob('knowi-core-*.cms'))),4)
            self.assertEqual(unrelated.read_bytes(),b'keep')

    def test_corrupt_new_archive_preserves_previous_points(self):
        import common
        from unittest.mock import patch
        base=Path(__file__).resolve().parents[3]/'output/knowi-backup-tests';base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as root:
            directory=Path(root);self.assertEqual(directory.resolve().parent,base.resolve())
            self.archive_fixture(directory,5)
            newest=directory/(self.points(5)[-1]['id']+'.cms')
            newest.write_bytes(b'x'*newest.stat().st_size)
            with patch.object(common,'ARCHIVES',directory):
                with self.assertRaises(RuntimeError):common.prune()
            self.assertEqual(len(list(directory.glob('knowi-core-*.cms'))),5)

    def test_failed_backup_keeps_four_previous_points(self):
        import backup,common,contextlib
        from types import SimpleNamespace
        from unittest.mock import patch
        base=Path(__file__).resolve().parents[3]/'output/knowi-backup-tests';base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as root:
            directory=Path(root);self.assertEqual(directory.resolve().parent,base.resolve())
            self.archive_fixture(directory,4)
            cfg=SimpleNamespace(read_text=lambda:'{}')
            with patch.object(common,'ARCHIVES',directory),patch.object(backup,'ARCHIVES',directory),patch.object(backup,'CONFIG',cfg),patch.object(backup,'lock',contextlib.nullcontext),patch.object(backup.shutil,'disk_usage',return_value=SimpleNamespace(total=1024**3,free=1024**3)),patch.object(backup,'source',side_effect=RuntimeError('synthetic_source_unavailable')):
                with self.assertRaisesRegex(RuntimeError,'synthetic_source_unavailable'):backup.backup()
            self.assertEqual(len(list(directory.glob('knowi-core-*.cms'))),4)

    def test_published_fifth_unacknowledged_point_does_not_block_next_backup(self):
        import backup,common,contextlib
        from types import SimpleNamespace
        from unittest.mock import patch
        base=Path(__file__).resolve().parents[3]/'output/knowi-backup-tests';base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as root:
            directory=Path(root);self.assertEqual(directory.resolve().parent,base.resolve())
            self.archive_fixture(directory,5)
            cfg=SimpleNamespace(read_text=lambda:'{}')
            with patch.object(common,'ARCHIVES',directory),patch.object(backup,'ARCHIVES',directory),patch.object(backup,'CONFIG',cfg),patch.object(backup,'lock',contextlib.nullcontext),patch.object(backup.shutil,'disk_usage',return_value=SimpleNamespace(total=1024**3,free=1024**3)),patch.object(backup,'source',side_effect=RuntimeError('reached_database_snapshot')):
                with self.assertRaisesRegex(RuntimeError,'reached_database_snapshot'):backup.backup()
            self.assertEqual(len(list(directory.glob('knowi-core-*.cms'))),4)

    def test_ack_older_archive_does_not_mark_latest_offsite_or_rotate(self):
        import common,receiver,contextlib,io,os
        from unittest.mock import patch
        base=Path(__file__).resolve().parents[3]/'output/knowi-backup-tests';base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as root:
            directory=Path(root);self.assertEqual(directory.resolve().parent,base.resolve())
            self.archive_fixture(directory,5)
            with patch.object(common,'ARCHIVES',directory),patch.object(receiver,'ARCHIVES',directory),patch.object(receiver,'ROOT',directory),patch.object(receiver,'lock',contextlib.nullcontext),patch('sys.stdout',io.StringIO()):
                oldest=common.records()[0]
                with patch.dict(os.environ,{'SSH_ORIGINAL_COMMAND':'ack '+oldest['id']+' '+oldest['sha256']}):receiver.main()
                status=json.loads((directory/'status.json').read_text())
                self.assertEqual(status['latest'],self.points(5)[-1]['id'])
                self.assertEqual(status['status'],'created_waiting_for_offsite')
                self.assertEqual(len(common.records()),5)

    def test_four_or_fewer_keep_every_recovery_point(self):
        for n in range(5): self.assertEqual(removable(self.points(n)),[])

    def test_path_arguments_cannot_escape_managed_backup_directory(self):
        for bad in ('../secret','knowi-core-../../root','a/b','knowi-core-20261113T000000Z-abcdef00.cms',''):
            with self.assertRaises(ValueError): checked_id(bad)

    def test_write_once_staging_does_not_overwrite_an_unknown_outcome(self):
        base=Path(__file__).resolve().parents[3]/'output/knowi-backup-tests'
        base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as root:
            self.assertEqual(Path(root).resolve().parent,base.resolve())
            path=Path(root)/'state.json'
            atomic_json(path,{'value':1})
            path.with_suffix('.json.new').write_text('interrupted')
            with self.assertRaises(FileExistsError): atomic_json(path,{'value':2})
            self.assertEqual(json.loads(path.read_text()),{'value':1})

    def test_open_log_writer_prevents_compression_and_removal(self):
        import logs
        from unittest.mock import patch
        base=Path(__file__).resolve().parents[3]/'output/knowi-backup-tests'
        base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as root:
            self.assertEqual(Path(root).resolve().parent,base.resolve())
            directory=Path(root)
            pending=directory/'one.pending';pending.write_bytes(b'not-yet-closed')
            with patch.object(logs,'ARCHIVE',directory), patch.object(logs,'PODS',directory), patch.object(logs,'writable_inodes',return_value={pending.stat().st_ino}):
                with self.assertRaises(RuntimeError): logs.finish({'stage':'one.pending','original':'source','running':False})
            self.assertEqual(pending.read_bytes(),b'not-yet-closed')
            self.assertFalse((directory/'one.log.gz').exists())

    def test_log_staging_rename_stays_in_original_directory(self):
        import logs
        from unittest.mock import patch
        base=Path(__file__).resolve().parents[3]/'output/knowi-backup-tests'
        base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as root:
            self.assertEqual(Path(root).resolve().parent,base.resolve())
            directory=Path(root);pods=directory/'pods';state=directory/'state';state.mkdir()
            source=pods/'ns_pod_uid'/'service'/'0.log';source.parent.mkdir(parents=True)
            source.write_bytes(b'closed-log')
            with patch.object(logs,'PODS',pods),patch.object(logs,'ARCHIVE',directory/'archives'),patch.object(logs,'STATE',state),patch.object(logs,'finish') as finish:
                logs.rotate(source)
                record=finish.call_args.args[0]
                pending=pods/record['stage']
                self.assertEqual(pending.parent,source.parent)
                self.assertTrue(pending.name.startswith('.knowi-'))
                self.assertEqual(pending.read_bytes(),b'closed-log')

    def test_interrupted_compression_rebuilds_only_own_partial_file(self):
        import logs,gzip
        from unittest.mock import patch
        base=Path(__file__).resolve().parents[3]/'output/knowi-backup-tests'
        base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as root:
            self.assertEqual(Path(root).resolve().parent,base.resolve())
            directory=Path(root);pending=directory/'one.pending';pending.write_bytes(b'complete-original')
            (directory/'one.log.tmp').write_bytes(b'interrupted-compression')
            with patch.object(logs,'ARCHIVE',directory),patch.object(logs,'PODS',directory),patch.object(logs,'writable_inodes',return_value=set()):
                logs.finish({'stage':'one.pending','original':'source','running':False})
            with gzip.open(directory/'one.log.gz','rb') as stream:self.assertEqual(stream.read(),b'complete-original')
            self.assertFalse(pending.exists())


if __name__=='__main__': unittest.main()
