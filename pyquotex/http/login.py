import re
import json
import sys
import asyncio
from pathlib import Path
from pyquotex.http.navigator import Browser


class Login(Browser):
    """Class for Quotex login resource."""

    url = ""
    cookies = None
    ssid = None
    # Updated official domains
    alternative_domains = ['qxbroker.com', 'quotex-broker.com', 'market-qx.trade', 'quotex.io', 'qx-broker.com']
    base_url = alternative_domains[0]
    https_base_url = f'https://{base_url}'

    def __init__(self, api, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.api = api
        self.html = None
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0"
        }
        self.full_url = f"{self.https_base_url}/{api.lang}"

    def get_token(self):
        """Attempts to fetch the CSRF token from multiple domains if blocked."""
        for domain in self.alternative_domains:
            try:
                self.base_url = domain
                self.https_base_url = f'https://{domain}'
                self.full_url = f"{self.https_base_url}/{self.api.lang}"
                
                # Direct sign-in page is often more stable than the modal
                target_url = f"{self.full_url}/sign-in/"
                self.send_request("GET", target_url, timeout=10)
                
                if self.response and self.response.status_code == 200:
                    html = self.get_soup()
                    match = html.find("input", {"name": "_token"})
                    if match:
                        token = match.get("value")
                        print(f"Bypass Successful on: {domain}")
                        return token
            except Exception as e:
                print(f"Failed bypass on {domain}: {e}")
                continue
        return None

    async def awaiting_pin(self, data, input_message):
        from pathlib import Path
        self.headers["Content-Type"] = "application/x-www-form-urlencoded"
        self.headers["Referer"] = f"{self.full_url}/sign-in/modal"
        data["keep_code"] = 1
        
        pin_file = Path("pin.txt")
        print(f"\n[QUOTEX] {input_message}")
        print("Note: You can also write the code into a 'pin.txt' file in the project folder.")
        
        code = None
        # Wait up to 2 minutes for pin.txt
        for i in range(60):
            if pin_file.exists():
                try:
                    content = pin_file.read_text().strip()
                    if content and content.isdigit():
                        code = content
                        print(f"PIN detected in pin.txt: {code}")
                        pin_file.unlink()
                        break
                except Exception:
                    pass
            await asyncio.sleep(2)
            
        if not code:
            try:
                print("No pin.txt found. Falling back to console input...")
                code = input(input_message)
            except (EOFError, Exception):
                print("Console input failed. Please use pin.txt to provide the PIN.")
                return

        if not code or not code.isdigit():
            print("Invalid code. Please try again.")
            await self.awaiting_pin(data, input_message)
            return

        data["code"] = code
        await asyncio.sleep(1)
        self.send_request(
            method="POST",
            url=f"{self.full_url}/sign-in/modal",
            data=data
        )

    def get_profile(self):
        self.response = self.send_request(
            method="GET",
            url=f"{self.full_url}/trade"
        )
        if self.response:
            # More robust search for window.settings
            soup = self.get_soup()
            scripts = soup.find_all("script")
            settings_script = None
            for s in scripts:
                txt = s.get_text()
                if txt and "window.settings =" in txt:
                    if '"token"' in txt or "'token'" in txt:
                        settings_script = txt
                        break
                    elif not settings_script:
                        settings_script = txt
            
            if not settings_script:
                return None, None

            # Extract JSON from script
            match = re.search(r"window\.settings\s*=\s*(\{.*?\});", settings_script, re.DOTALL)
            if not match:
                # Try without trailing semicolon if it's missing or different
                match = re.search(r"window\.settings\s*=\s*(\{.*\})", settings_script, re.DOTALL)
            
            if match:
                match_str = match.group(1)
                try:
                    data = json.loads(match_str)
                    self.cookies = self.get_cookies()
                    self.ssid = data.get("token")
                    self.api.session_data["cookies"] = self.cookies
                    self.api.session_data["token"] = self.ssid
                    self.api.session_data["user_agent"] = self.headers["User-Agent"]
                    output_file = Path(f"{self.api.resource_path}/session.json")
                    output_file.parent.mkdir(exist_ok=True, parents=True)
                    output_file.write_text(
                        json.dumps({
                            "cookies": self.cookies,
                            "token": self.ssid,
                            "user_agent": self.headers["User-Agent"]
                        }, indent=4)
                    )
                    return self.response, data
                except json.JSONDecodeError as e:
                    return None, None
            else:
                return None, None

        return None, None

    def _get(self):
        return self.send_request(
            method="GET",
            url=f"f{self.full_url}/trade"
        )

    async def _post(self, data):
        """Send get request for Quotex API login http resource.
        :returns: The instance of :class:`requests.Response`.
        """
        self.response = self.send_request(
            method="POST",
            url=f"{self.full_url}/sign-in/",
            data=data
        )

        required_keep_code = self.get_soup().find(
            "input", {"name": "keep_code"}
        )
        if required_keep_code:
            auth_body = self.get_soup().find(
                "main", {"class": "auth__body"}
            )
            input_message = (
                f'{auth_body.find("p").text}: ' if auth_body.find("p")
                else "Insira o código PIN que acabamos "
                     "de enviar para o seu e-mail: "
            )
            await self.awaiting_pin(data, input_message)
        await asyncio.sleep(1)
        success = self.success_login()
        return success

    def success_login(self):
        if self.response.url.endswith("/trade") or "/trade/" in self.response.url:
            return True, "Login successful."
        html = self.get_soup()
        match = html.find(
            "div", {"class": "hint--danger"}
        ) or html.find(
            "div", {"class": "input-control-cabinet__hint"}
        )
        message_in_match = match.text.strip() if match else ""
        return False, f"Login failed. {message_in_match}"

    async def __call__(self, username, password, user_data_dir=None):
        """Method to get Quotex API login http request.
        :param str username: The username of a Quotex server.
        :param str password: The password of a Quotex server.
        :param str user_data_dir: The optional value for path userdata.
        :returns: The instance of :class:`requests.Response`.
        """
        data = {
            "_token": self.get_token(),
            "email": username,
            "password": password,
            "remember": 1,

        }
        status, msg = await self._post(data)
        if not status:
            print(msg)
            exit(0)

        self.get_profile()

        return status, msg
