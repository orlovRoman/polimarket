import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_retry = Retry(
    total=3, backoff_factor=1.0,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "OPTIONS", "POST"]
)
_adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=_retry)

shared_session = requests.Session()
shared_session.mount("https://", _adapter)
shared_session.mount("http://",  _adapter)
