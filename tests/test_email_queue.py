from daglas.email_queue import EmailQueue, RawEmail


class TestPushAndPop:
    def test_push_and_pop(self, tmp_path):
        q = EmailQueue(data_dir=str(tmp_path))
        email = RawEmail("a@b.com", "hej", "hello", b"raw")
        q.push("incoming", email)
        result = q.pop("incoming")
        assert result is not None
        assert result.sender == "a@b.com"
        assert result.subject == "hej"
        assert result.body == "hello"
        assert result.raw_bytes == b"raw"

    def test_empty_pop(self, tmp_path):
        q = EmailQueue(data_dir=str(tmp_path))
        assert q.pop("incoming") is None

    def test_fifo_order(self, tmp_path):
        q = EmailQueue(data_dir=str(tmp_path))
        q.push("incoming", RawEmail("a@b.com", "s1", "b1", b"r1"))
        q.push("incoming", RawEmail("b@b.com", "s2", "b2", b"r2"))
        q.push("incoming", RawEmail("c@b.com", "s3", "b3", b"r3"))
        assert q.pop("incoming").sender == "a@b.com"
        assert q.pop("incoming").sender == "b@b.com"
        assert q.pop("incoming").sender == "c@b.com"
        assert q.pop("incoming") is None

    def test_notify_called_on_push(self, tmp_path):
        q = EmailQueue(data_dir=str(tmp_path))
        calls = []
        q.on_push("incoming", lambda ns: calls.append(ns))
        q.push("incoming", RawEmail("a@b.com", "s", "b", b"r"))
        assert calls == ["incoming"]

    def test_notify_exception_does_not_block(self, tmp_path):
        q = EmailQueue(data_dir=str(tmp_path))

        def broken(_ns):
            raise RuntimeError("boom")

        calls = []
        q.on_push("incoming", broken)
        q.on_push("incoming", lambda ns: calls.append(ns))
        q.push("incoming", RawEmail("a@b.com", "s", "b", b"r"))
        assert calls == ["incoming"]

    def test_raw_bytes_roundtrip(self, tmp_path):
        q = EmailQueue(data_dir=str(tmp_path))
        original = RawEmail("a@b.com", "s", "b", b"\x00\xff\xfe\xfd")
        q.push("incoming", original)
        result = q.pop("incoming")
        assert result.raw_bytes == original.raw_bytes

    def test_namespace_isolation(self, tmp_path):
        q = EmailQueue(data_dir=str(tmp_path))
        q.push("ns1", RawEmail("a@b.com", "s", "b", b"r"))
        assert q.pop("ns2") is None


class TestDrain:
    def test_drain_returns_all(self, tmp_path):
        q = EmailQueue(data_dir=str(tmp_path))
        q.push("incoming", RawEmail("a@b.com", "s1", "b1", b"r1"))
        q.push("incoming", RawEmail("b@b.com", "s2", "b2", b"r2"))
        emails = q.drain("incoming")
        assert len(emails) == 2
        assert emails[0].sender == "a@b.com"
        assert emails[1].sender == "b@b.com"
        assert q.pop("incoming") is None

    def test_drain_empty(self, tmp_path):
        q = EmailQueue(data_dir=str(tmp_path))
        assert q.drain("incoming") == []
