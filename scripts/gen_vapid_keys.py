"""Generate a VAPID key pair for Web Push.

    python -m scripts.gen_vapid_keys

Prints the two values to put in your environment. Run it ONCE per deployment and
keep the private key secret — rotating it invalidates every existing
subscription, because every stored endpoint was signed with the old pair and
every reader would silently stop receiving pushes until they re-subscribed.
"""

import base64

from py_vapid import Vapid01
from cryptography.hazmat.primitives import serialization


def main() -> None:
    vapid = Vapid01()
    vapid.generate_keys()

    private_raw = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
    public_raw = vapid.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    print("VAPID_PRIVATE_KEY=" + b64(private_raw))
    print("VAPID_PUBLIC_KEY=" + b64(public_raw))
    print("VAPID_SUBJECT=mailto:you@yourdomain")
    print()
    print("Put these in your .env. Keep the private key secret — rotating it")
    print("silently unsubscribes every device you have.")


if __name__ == "__main__":
    main()
