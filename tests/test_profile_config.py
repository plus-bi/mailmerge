from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
import pytest

from mailmerge.db import Base
from mailmerge.models import Profile
from mailmerge.profile_config import load_profiles


def test_profile_config_creates_and_updates_profile(tmp_path):
    path = tmp_path / "profiles.toml"
    path.write_text('''
[[profiles]]
name = "TUM"
smtp_host = "postout.lrz.de"
smtp_port = 587
security = "starttls"
imap_host = "xmail.mwn.de"
imap_port = 993
imap_security = "tls"
''')
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        loaded = load_profiles(path, db)
        assert loaded[0].imap_host == "xmail.mwn.de"
        path.write_text(path.read_text().replace("smtp_port = 587", "smtp_port = 465").replace('security = "starttls"', 'security = "tls"'))
        load_profiles(path, db)
        profiles = db.scalars(select(Profile)).all()
        assert len(profiles) == 1
        assert profiles[0].smtp_port == 465


def test_profile_config_rejects_plaintext_password_field(tmp_path):
    path = tmp_path / "profiles.toml"
    path.write_text('''
[[profiles]]
name = "unsafe"
smtp_host = "example.com"
password = "should-not-be-read"
''')
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        with pytest.raises(ValueError, match="contains a secret"):
            load_profiles(path, db)
