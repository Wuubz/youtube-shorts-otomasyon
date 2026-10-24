"""Tek seferlik OAuth2 kurulumu: bir YouTube kanalı için REFRESH_TOKEN alır.

Kullanım:
    python scripts/get_refresh_token.py                      # Client ID / Secret'ı sorar
    python scripts/get_refresh_token.py --client-secret-file client_secret.json
    python scripts/get_refresh_token.py --prefix YT_CH1      # çıktıda secret adlarını gösterir
"""
import argparse
import getpass
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

# youtube.readonly yalnızca kurulumda kanal adını doğrulamak içindir (channels.list, 1 kota birimi);
# uygulama (youtube_uploader.py) çalışırken yalnızca youtube.upload kullanır.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"


def build_flow(args: argparse.Namespace) -> InstalledAppFlow:
    if args.client_secret_file:
        return InstalledAppFlow.from_client_secrets_file(args.client_secret_file, SCOPES)
    client_id = args.client_id or input("CLIENT_ID: ").strip()
    client_secret = args.client_secret or getpass.getpass("CLIENT_SECRET (yazarken görünmez): ").strip()
    if not client_id or not client_secret:
        sys.exit("HATA: CLIENT_ID ve CLIENT_SECRET boş olamaz.")
    config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
            "redirect_uris": ["http://localhost"],
        }
    }
    return InstalledAppFlow.from_client_config(config, SCOPES)


def print_channel(credentials) -> None:
    """Token'ın bağlı olduğu kanalın adını ve ID'sini yazdırır (channels.list, 1 kota birimi)."""
    try:
        from googleapiclient.discovery import build

        service = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        items = service.channels().list(part="snippet", mine=True).execute().get("items", [])
    except Exception as exc:  # doğrulama başarısız olsa da token çıktısı kaybolmasın
        print(f"\nUYARI: Kanal adı doğrulanamadı: {exc}")
        return
    if not items:
        print("\nUYARI: Bu hesapta YouTube kanalı bulunamadı; doğru marka kanalını seçtiğinizden emin olun.")
    for item in items:
        print(f"\nBağlanan kanal: {item['snippet']['title']}  (ID: {item['id']})")


def main() -> None:
    parser = argparse.ArgumentParser(description="YouTube kanalı için OAuth2 REFRESH_TOKEN alır.")
    parser.add_argument("--client-secret-file", help="Google Cloud'dan indirilen client_secret.json yolu")
    parser.add_argument("--client-id", help="OAuth Client ID (verilmezse sorulur)")
    parser.add_argument("--client-secret", help="OAuth Client Secret (verilmezse sorulur)")
    parser.add_argument("--prefix", default="YT_CHx", help="GitHub Secret öneki, ör. YT_CH1")
    args = parser.parse_args()

    flow = build_flow(args)
    print("\nTarayıcı açılıyor... İlgili YouTube kanalının bağlı olduğu Google hesabını (veya marka kanalını) seçip izin verin.\n")
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    if not credentials.refresh_token:
        sys.exit(
            "HATA: Refresh token alınamadı. https://myaccount.google.com/permissions adresinden bu "
            "uygulamanın erişimini kaldırıp betiği yeniden çalıştırın."
        )

    print_channel(credentials)

    print("\n" + "=" * 70)
    print("BAŞARILI! Aşağıdaki değerleri GitHub Secrets'a ekleyin:\n")
    print(f"{args.prefix}_CLIENT_ID      = {flow.client_config['client_id']}")
    print(f"{args.prefix}_CLIENT_SECRET  = {flow.client_config['client_secret']}")
    print(f"{args.prefix}_REFRESH_TOKEN  = {credentials.refresh_token}")
    print("=" * 70)
    print("Bu değerleri kimseyle paylaşmayın ve koda/depoya yazmayın.")


if __name__ == "__main__":
    main()
