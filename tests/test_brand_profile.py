"""Marka profili (Client.brand_profile) + global ayarlar (AppSetting) veri modeli."""
from extensions import db
from models import AppSetting, Client


def test_appsetting_set_get_round_trip(app):
    with app.app_context():
        assert AppSetting.get("caption_global_rules") is None
        assert AppSetting.get("caption_global_rules", default="varsayilan") == "varsayilan"

        AppSetting.set("caption_global_rules", "# Global kurallar\n- kural 1")
        db.session.commit()

        assert AppSetting.get("caption_global_rules") == "# Global kurallar\n- kural 1"


def test_appsetting_set_ikinci_cagri_gunceller(app):
    with app.app_context():
        AppSetting.set("caption_global_rules", "ilk")
        db.session.commit()
        AppSetting.set("caption_global_rules", "ikinci")
        db.session.commit()

        assert AppSetting.get("caption_global_rules") == "ikinci"
        assert db.session.query(AppSetting).count() == 1


def test_client_brand_profile_yazilir_okunur(app):
    with app.app_context():
        c = Client(name="Marka Profili Test")
        db.session.add(c)
        db.session.commit()

        assert c.brand_profile is None

        c.brand_profile = {
            "brand_voice": "güvenilir, uzman",
            "target_audience": "B2B işletmeler",
            "forbidden": ["—", "aşırı emoji"],
            "cta": "DM'den yazın",
            "guide_md": "# Rehber\nİçerik...",
        }
        db.session.commit()

        yeniden = db.session.get(Client, c.id)
        assert yeniden.brand_profile["brand_voice"] == "güvenilir, uzman"
        assert yeniden.brand_profile["guide_md"] == "# Rehber\nİçerik..."
