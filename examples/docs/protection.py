"""Solve one response challenge and replay the rejected request."""

from examples.mail.http.captcha import login_after_captcha


# region docs: protection-run
# examples/docs/protection.py
def main() -> None:
    challenge, requests, cookie, next_step = login_after_captcha()
    print(f"challenge: {challenge}")
    print(f"password requests: {requests}")
    print(f"clearance cookie: {cookie}")
    print(f"result: {next_step}")
# endregion docs: protection-run


if __name__ == "__main__":
    main()
