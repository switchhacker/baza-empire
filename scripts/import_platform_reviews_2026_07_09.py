#!/usr/bin/env python3
"""One-shot import of AHBCO's real Thumbtack + HomeAdvisor reviews (scraped
2026-07-09 via Phantom Browser, screenshots in dashboard/artifacts/browser/).

Writes standard review JSON files into dashboard/artifacts/ahb123-reviews/ so
they appear in the Reviews tab (/api/reviews/all) and, when published, on
ahb123.com via /api/reviews/published. Follows the existing moderation policy:
4-5 star auto-publish, 1-3 star held for manual approval.

Idempotent: skips a file if it already exists.
"""
import datetime
import json
import os

REVIEWS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "dashboard", "artifacts", "ahb123-reviews")

TT_URL = "https://www.thumbtack.com/pa/bensalem/carpenters/all-home-building-co/service/499192842088882194"
HA_URL = "https://www.homeadvisor.com/rated.AllHomeBuildingCompany.129386938.html"

REVIEWS = [
    # ---- Thumbtack (profile header: Good 4.1, 10 reviews) ----
    dict(name="Catherine C.", stars=5, date="2023-12-15", source="thumbtack",
         project_type="Home Renovation",
         text="From the beginning to the end of the entire renovation home project, I was very pleased with the entire result of transforming my entire home. They always had wonderful suggestions to help me to stay on budget and offer great design ideas from paint colors to kitchen tiles etc. I would definitely use this company again for my next project."),
    dict(name="Tracey F.", stars=4, date="2023-12-05", source="thumbtack",
         project_type="Flooring & Tile",
         text="Installed tile floor in my kitchen and laminate floor in a bedroom and a few additional things. Very nice guys and I would call them in to do additional work."),
    dict(name="Ryan R.", stars=5, date="2023-10-30", source="thumbtack",
         project_type="Kitchen Remodel",
         text="I had AHB complete a full kitchen rehab for me a few months back. They answered every question I had along the way, and they got to my job quickly. I will be using them in the future and I would recommend AHB to whoever needs work done, for projects big or small!"),
    dict(name="Wendy S.", stars=5, date="2023-10-28", source="thumbtack",
         project_type="Deck & Porch",
         text="Serg and his team did such an amazing job on my porch awning and deck. The whole job was finished within a week. The guys cleaned up as they went along. I highly recommend him to anyone that is looking to build a deck/porch."),
    dict(name="Sina D.", stars=5, date="2024-07-03", source="thumbtack",
         project_type="",
         text="They did an excellent job. All work was completed before pre finished date. They cleaned up behind completed job. Very professional."),
    dict(name="Timothy Z.", stars=5, date="2023-12-04", source="thumbtack",
         project_type="",
         text="They were fairly priced, and did an excellent job in the time they said it would take to do the job. They get my thumbs up on the job and recommendation."),
    dict(name="Zar Z.", stars=5, date="2023-11-01", source="thumbtack",
         project_type="Kitchen Remodel",
         text="AHB Company exceeded my expectations! They built the kitchen I dreamed of!! I'm so happy with their work!"),
    dict(name="Jb J.", stars=1, date="2023-12-12", source="thumbtack",
         project_type="Bathroom Remodel",
         text="Was late for the original appointment and we had to reschedule, did not show for the second appointment and did not call."),
    dict(name="Courtney V.", stars=5, date="2023-11-18", source="thumbtack",
         project_type="Deck or Porch Repair",
         text="Fast, fair, responsive. Highly recommended!"),
    dict(name="Roberto C.", stars=1, date="2024-03-02", source="thumbtack",
         project_type="Deck or Porch Remodel",
         text="Never followed up."),
    # ---- HomeAdvisor (profile header: 5.0, 5 reviews, all Verified, Mar 2023) ----
    dict(name="Oleg L.", stars=5, date="2023-03-05", display_date="Mar 2023", source="homeadvisor",
         project_type="",
         text="Serge and his guys did a great job. Communication was key, and the work was top quality. I was very satisfied with the work and I would use them again in the future."),
    dict(name="Mike L.", stars=5, date="2023-03-04", display_date="Mar 2023", source="homeadvisor",
         project_type="Brick Paved Patio",
         text="Hired them to construct me a brick paved patio. They did an excellent job. Extremely satisfied with their service."),
    dict(name="Nastya P.", stars=5, date="2023-03-03", display_date="Mar 2023", source="homeadvisor",
         project_type="Bathroom Remodel",
         text="Serg and his team did a wonderful job on my bathroom remodel. I was very happy with the communication between Serg and I. I had a vision and the team made it come to life. They were very organized, punctual, and professional. We had some issues with our bathroom prior to the remodel and Serg and his team were able to fix them. I was also surprised with how reasonably priced their services are for such great work. I highly recommend Serg and his team."),
    dict(name="Bill M.", stars=5, date="2023-03-02", display_date="Mar 2023", source="homeadvisor",
         project_type="",
         text="Serge and his crew are all terrific. They presented creative design and materials ideas and the job turned out better than we first expected. My wife and I strongly recommend them in value, quality, and promptly finishing on time."),
    dict(name="Catherine C.", stars=5, date="2023-03-01", display_date="Mar 2023", source="homeadvisor",
         project_type="",
         text="I can't be more happier to work with this company. They offered many amazing designs and practical ideas and solutions and helping me to stay within budget and time frame. The crews are absolutely trustworthy, professional, offered great communication and they will get the job done right. They gave me the best quotes among many competitors in the areas that had great references. I will definitely reach out to them for my next attic addition."),
]

PLATFORM_LABEL = {"thumbtack": "Thumbtack", "homeadvisor": "HomeAdvisor"}
SOURCE_URLS = {"thumbtack": TT_URL, "homeadvisor": HA_URL}


def main():
    os.makedirs(REVIEWS_DIR, exist_ok=True)
    written = skipped = 0
    for r in REVIEWS:
        d = datetime.datetime.strptime(r["date"], "%Y-%m-%d").replace(hour=12)
        ts = int(d.timestamp())
        fname = f"review_{ts}.json"
        fpath = os.path.join(REVIEWS_DIR, fname)
        if os.path.exists(fpath):
            skipped += 1
            continue
        rec = {
            "stars": r["stars"],
            "name": r["name"],
            "text": r["text"],
            "project_type": r["project_type"],
            "tags": [PLATFORM_LABEL[r["source"]]],
            "email": "",
            "phone": "",
            "date": r.get("display_date", r["date"]),
            "ts": d.isoformat(),
            "source": r["source"],
            "source_url": SOURCE_URLS[r["source"]],
            "imported_at": datetime.datetime.now().isoformat(),
            # Existing moderation policy: 4-5 star auto-publish, 1-3 star pending.
            "published": r["stars"] >= 4,
        }
        with open(fpath, "w") as f:
            json.dump(rec, f, indent=2)
        written += 1
    total = len(REVIEWS)
    pub = [r for r in REVIEWS if r["stars"] >= 4]
    avg_all = sum(r["stars"] for r in REVIEWS) / total
    avg_pub = sum(r["stars"] for r in pub) / len(pub)
    print(f"written={written} skipped={skipped} total={total}")
    print(f"avg_all={avg_all:.2f}  avg_published={avg_pub:.2f} ({len(pub)} published)")


if __name__ == "__main__":
    main()
