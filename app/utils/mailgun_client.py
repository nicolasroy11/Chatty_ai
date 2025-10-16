import requests
import runtime_settings as rt

def send_email(to: str, subject: str, text: str) -> bool:
    """
    Send a simple plaintext email via Mailgun API.
    Uses shared Mailgun credentials from runtime_settings.
    """
    domain = rt.MAILGUN_DOMAIN
    api_key = rt.MAILGUN_API_KEY

    try:
        response = requests.post(
            f"https://api.mailgun.net/v3/{domain}/messages",
            auth=("api", api_key),
            data={
                "from": f"Chatty.io Notifications <postmaster@{domain}>",
                "to": [to],
                "subject": subject,
                "text": text,
            },
            timeout=10
        )
        response.raise_for_status()
        print(f"[Mailgun] Email sent to {to} (status {response.status_code})")
        return True

    except requests.RequestException as e:
        print(f"[Mailgun ERROR] {e}")
        return False
