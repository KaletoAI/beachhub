import argparse
import getpass

from beachhub_core.database import SessionLocal


def main() -> None:
    p = argparse.ArgumentParser(prog="beachhub-core")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin")
    a.add_argument("--name", required=True)
    a.add_argument("--rolle", default="admin", choices=["admin", "lesend"])
    sub.add_parser("keygen")
    m = sub.add_parser("monatslauf")
    m.add_argument("monat", help="JJJJ-MM")
    args = p.parse_args()
    if args.cmd == "create-admin":
        from beachhub_core import auth

        pw = getpass.getpass("Passwort (min. 12 Zeichen): ")
        with SessionLocal() as db:
            user, secret = auth.lege_admin_an(db, name=args.name, passwort=pw, rolle=args.rolle)
            db.commit()
        import pyotp

        print(f"Admin '{user.name}' angelegt. TOTP-Secret: {secret}")
        print(pyotp.TOTP(secret).provisioning_uri(name=user.name, issuer_name="Beachhub"))
    elif args.cmd == "keygen":
        from beachhub_core.services import lesestand

        print(lesestand.erzeuge_schluessel())
    elif args.cmd == "monatslauf":
        from beachhub_core import jobs

        jahr, monat = (int(x) for x in args.monat.split("-"))
        with SessionLocal() as db:
            anzahl = jobs.monatslauf_fuer(db, jahr, monat)
        print(f"{anzahl} Rechnungen erzeugt")
