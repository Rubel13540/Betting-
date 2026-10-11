"""
Facebook Graph API + Web scraping helpers (refactored from your CLI tool)
"""
import re
import random
import requests


def get_random_ua() -> str:
    android_versions = ["9", "10", "11", "12", "13", "14"]
    chrome_builds = [
        "118.0.5993.80", "119.0.6045.163", "120.0.6099.144",
        "121.0.6167.101", "122.0.6261.64",
    ]
    devices = ["SM-G991B", "SM-A525F", "Redmi Note 10", "Pixel 6", "POCO X3", "Realme 8"]
    return (
        f"Mozilla/5.0 (Linux; Android {random.choice(android_versions)}; "
        f"{random.choice(devices)}) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{random.choice(chrome_builds)} Mobile Safari/537.36"
    )


def get_headers(cookie_str: str = "") -> dict:
    headers = {
        "user-agent": get_random_ua(),
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9,bn;q=0.8",
        "sec-ch-ua-mobile": "?1",
        "upgrade-insecure-requests": "1",
    }
    if cookie_str:
        headers["cookie"] = cookie_str
    return headers


def login_with_cookie(cookie: str) -> dict:
    """
    Returns {"success": True, "token": ..., "uid": ..., "name": ...}
    or {"success": False, "error": "..."}
    """
    try:
        head = get_headers(cookie)
        ses = requests.Session()
        data = ses.get(
            "https://business.facebook.com/business_locations",
            headers=head, timeout=15,
        )
        token_match = re.search(r"(EAA[A-Za-z0-9]+)", data.text)
        if not token_match:
            return {"success": False, "error": "Token not found in cookie"}
        token = token_match.group(1)

        me = ses.get(
            f"https://graph.facebook.com/me?fields=name,id&access_token={token}",
            headers=head, timeout=15,
        ).json()

        if "id" not in me:
            return {"success": False, "error": "Invalid cookie / token"}

        return {
            "success": True,
            "token": token,
            "uid": me["id"],
            "name": me.get("name", "Unknown"),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_post_id(link: str):
    try:
        link = link.split("?")[0].split("&")[0].replace("m.facebook.com", "facebook.com")
        if "/posts/" in link:
            return link.split("/posts/")[1].split("/")[0]
        if "fbid=" in link:
            return link.split("fbid=")[1].split("&")[0]
        if "/photos/" in link:
            return link.split("/photos/")[1].split("/")[0]
        if "/story.php" in link:
            return link.split("story_fbid=")[1].split("&")[0]
        for x in link.split("/"):
            if x and x.isdigit() and len(x) > 8:
                return x
        return None
    except Exception:
        return None


def post_comment(post_id: str, token: str, cookie: str, message: str) -> dict:
    """Post a comment on a Facebook post."""
    try:
        ses = requests.Session()
        head = get_headers(cookie)
        res = ses.post(
            f"https://graph.facebook.com/{post_id}/comments",
            data={"message": message, "access_token": token},
            headers=head, timeout=15,
        ).json()

        if "id" in res:
            return {"success": True, "comment_id": res["id"]}
        err = res.get("error", {}).get("message", "Failed")
        return {"success": False, "error": err}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_groups(token: str, cookie: str, limit: int = 500) -> list:
    """Fetch joined groups."""
    try:
        ses = requests.Session()
        head = get_headers(cookie)
        res = ses.get(
            f"https://graph.facebook.com/me/groups?limit={limit}&access_token={token}",
            headers=head, timeout=20,
        ).json()
        return res.get("data", [])
    except Exception:
        return []


def share_to_group(group_id: str, token: str, cookie: str, post_link: str) -> dict:
    """Share a post to a group."""
    try:
        ses = requests.Session()
        head = get_headers(cookie)
        res = ses.post(
            f"https://graph.facebook.com/{group_id}/feed",
            data={"link": post_link, "access_token": token},
            headers=head, timeout=15,
        ).json()

        if "id" in res:
            return {"success": True, "post_id": res["id"]}
        err = res.get("error", {}).get("message", "Failed")
        return {"success": False, "error": err}
    except Exception as e:
        return {"success": False, "error": str(e)}


def react_to_post(post_id: str, token: str, cookie: str, react_type: str = "LIKE") -> dict:
    """
    react_type: LIKE / LOVE / CARE / HAHA / WOW / SAD / ANGRY
    Tries Graph API first, then web fallback.
    """
    # Method 1: Graph API
    try:
        ses = requests.Session()
        head = get_headers(cookie)
        res = ses.post(
            f"https://graph.facebook.com/{post_id}/reactions",
            data={"type": react_type, "access_token": token},
            headers=head, timeout=15,
        ).json()

        if res.get("success") or "id" in res:
            return {"success": True, "method": "graph_api"}
    except Exception:
        pass

    # Method 2: Web fallback (mbasic)
    try:
        ses = requests.Session()
        head = get_headers(cookie)
        code_map = {
            "LIKE": "1", "LOVE": "2", "CARE": "16",
            "HAHA": "4", "WOW": "3", "SAD": "7", "ANGRY": "8",
        }
        code = code_map.get(react_type, "1")

        r1 = ses.get(
            f"https://mbasic.facebook.com/reactions/picker/?ft_id={post_id}",
            headers=head, timeout=15,
        )
        match = re.search(
            rf'href="(/ufi/reaction/\?ft_id={post_id}&amp;reaction_type={code}[^"]+)"',
            r1.text,
        )
        if not match:
            match = re.search(
                rf'href="(/ufi/reaction/\?[^"]*reaction_type={code}[^"]*)"',
                r1.text,
            )

        if match:
            react_url = "https://mbasic.facebook.com" + match.group(1).replace("&amp;", "&")
            head2 = get_headers(cookie)
            r2 = ses.get(react_url, headers=head2, timeout=15)
            if r2.status_code == 200:
                return {"success": True, "method": "web"}
            return {"success": False, "error": "Web request failed"}

        return {"success": False, "error": "Reaction link not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}


DEFAULT_COMMENTS = [
    "অসাধারণ পোস্ট ভাই",
    "খুবই সুন্দর",
    "মাশাল্লাহ্",
    "দারুন লাগলো",
    "ভাই লাভ ইউ",
    "ঝাক্কাস",
    "অস্থির লাগতেছে",
    "রাজা আপনি",
    "সুপার হিট",
    "ভাই একদম জোস্স",
    "একেবারে আগুন পোস্ট 🔥",
    "এক কথায় সেরা!",
    "প্রিয় ভাই আমার ❤️",
    "মন ছুঁয়ে গেল ভাই",
    "একদম সঠিক কথা বলছেন 👍",
  ]
