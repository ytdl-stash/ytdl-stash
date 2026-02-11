"""Test which adult sites support channel/profile scanning via yt-dlp.

Tries extract_flat on a sample channel URL for each site and reports
how many video entries are returned (capped at 5 to keep it fast).
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yt_dlp

TEST_CHANNELS = {
    "PornHub (model)": "https://www.pornhub.com/model/lena-paul/videos",
    "PornHub (pornstar)": "https://www.pornhub.com/pornstar/lena-paul/videos",
    "PornHub (channel)": "https://www.pornhub.com/channels/brazzers/videos",
    "XHamster": "https://xhamster.com/users/brazzers",
    "SpankBang": "https://spankbang.com/profile/brazzers/videos",
    "YouPorn": "https://www.youporn.com/channel/brazzers/",
    "RedTube": "https://www.redtube.com/pornstar/lena-paul",
    "Eporner": "https://www.eporner.com/profile/brazzers/",
    "XNXX": "https://www.xnxx.com/pornstar/lena-paul",
    "XVideos (model)": "https://www.xvideos.com/models/lena-paul",
    "XVideos (pornstar)": "https://www.xvideos.com/pornstars/lena-paul",
    "Motherless": "https://motherless.com/u/admin",
    "iwara": "https://www.iwara.tv/profile/orrientalenby",
    "RedGifs": "https://www.redgifs.com/users/petitejadex",
    "Tube8": "https://www.tube8.com/pornstar/lena-paul",
    "4tube": "https://www.4tube.com/pornstars/lena-paul",
}

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
WARN = "\033[93mWARN\033[0m"


def test_channel(name: str, url: str) -> dict:
    opts = {
        "extract_flat": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "playlistend": 5,
        "socket_timeout": 15,
    }
    result = {"name": name, "url": url, "status": None, "entries": 0, "extractor": None, "error": None}

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            result["status"] = "no_info"
            result["error"] = "No info returned"
            return result

        result["extractor"] = info.get("extractor") or info.get("extractor_key")

        entries = info.get("entries")
        if entries:
            entries = list(entries)
            result["entries"] = len(entries)

        if result["entries"] > 0:
            result["status"] = "ok"
        elif result["extractor"] == "generic":
            result["status"] = "generic_only"
            result["error"] = "Fell through to generic extractor"
        else:
            result["status"] = "no_entries"
            result["error"] = f"Extractor matched ({result['extractor']}) but returned 0 entries"

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    return result


def main():
    print(f"\n{'=' * 70}")
    print(f"  yt-dlp channel scanning support test  (yt-dlp {yt_dlp.version.__version__})")
    print(f"{'=' * 70}\n")

    results = []
    for name, url in TEST_CHANNELS.items():
        print(f"  Testing {name}...", end="", flush=True)
        r = test_channel(name, url)
        results.append(r)

        if r["status"] == "ok":
            print(f"\r  {PASS}  {name:<25} {r['entries']} entries  ({r['extractor']})")
        elif r["status"] == "no_entries":
            print(f"\r  {WARN}  {name:<25} 0 entries   ({r['extractor']})")
        else:
            err_short = (r["error"] or "unknown")[:45]
            print(f"\r  {FAIL}  {name:<25} {err_short}")

    # Summary
    passed = [r for r in results if r["status"] == "ok"]
    warned = [r for r in results if r["status"] == "no_entries"]
    failed = [r for r in results if r["status"] not in ("ok", "no_entries")]

    print(f"\n{'=' * 70}")
    print(f"  {PASS} {len(passed)} passed    {WARN} {len(warned)} no entries    {FAIL} {len(failed)} failed")
    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    main()
