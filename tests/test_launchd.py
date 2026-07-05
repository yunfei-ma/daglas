import plistlib

from scripts.install_launchd import HEARTBEAT_LABEL, _build_heartbeat_plist


class TestHeartbeatPlist:
    def test_has_correct_label(self):
        plist = _build_heartbeat_plist()
        assert plist["Label"] == HEARTBEAT_LABEL

    def test_run_at_load_is_true(self):
        plist = _build_heartbeat_plist()
        assert plist["RunAtLoad"] is True

    def test_keep_alive_is_true(self):
        plist = _build_heartbeat_plist()
        assert plist["KeepAlive"] is True

    def test_program_arguments_use_module(self):
        plist = _build_heartbeat_plist()
        args = plist["ProgramArguments"]
        assert "-m" in args
        assert "daglas.run" in args

    def test_no_generate_flag(self):
        plist = _build_heartbeat_plist()
        args = " ".join(plist["ProgramArguments"])
        assert "--generate" not in args

    def test_has_working_directory(self):
        plist = _build_heartbeat_plist()
        assert "WorkingDirectory" in plist

    def test_no_start_interval(self):
        plist = _build_heartbeat_plist()
        assert "StartInterval" not in plist

    def test_plist_serializes_to_valid_xml(self):
        plist = _build_heartbeat_plist()
        xml = plistlib.dumps(plist)
        parsed = plistlib.loads(xml)
        assert parsed["Label"] == HEARTBEAT_LABEL
