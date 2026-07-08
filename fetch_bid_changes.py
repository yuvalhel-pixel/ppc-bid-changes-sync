"""
Fetches last 7 days of bid-related changes from Google Ads
and writes the result as bid-changes.json to this repo.

Runs via GitHub Actions on a schedule.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta

import requests


def get_token():
    resp = requests.post("https://oauth2.googleapis.com/token", data={
        "grant_type": "refresh_token",
        "refresh_token": os.environ["GOOGLE_ADS_REFRESH_TOKEN"],
        "client_id": os.environ["GOOGLE_ADS_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_ADS_CLIENT_SECRET"],
    }, timeout=10)
    resp.raise_for_status()
    return resp.json()["access_token"]


def search(token, gaql):
    customer_id = "1270565523"  # Wix Search EN
    headers = {
        "Authorization": f"Bearer {token}",
        "developer-token": os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"],
        "login-customer-id": os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "5156996580"),
        "Content-Type": "application/json",
    }
    resp = requests.post(
        f"https://googleads.googleapis.com/v24/customers/{customer_id}/googleAds:search",
        headers=headers,
        json={"query": gaql},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def extract_portfolio_num(name):
    m = re.search(r'portfolio[_\s-]*(\d+)', str(name or ''), re.IGNORECASE)
    return int(m.group(1)) if m else None


def main():
    token = get_token()
    since = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
    until = datetime.utcnow().strftime("%Y-%m-%d")

    # Fetch active campaigns
    campaign_rows = search(token, """
        SELECT campaign.name, campaign.resource_name, campaign.bidding_strategy
        FROM campaign WHERE campaign.status = ENABLED
    """)
    resource_to_name = {}
    strategy_to_campaigns = {}
    for row in campaign_rows:
        c = row.get("campaign", {})
        rn = c.get("resourceName", "")
        name = c.get("name", "")
        strategy = c.get("biddingStrategy", "")
        if rn:
            resource_to_name[rn] = name
        if strategy and name:
            strategy_to_campaigns.setdefault(strategy, []).append(name)

    # Fetch bidding strategy names/tCPA
    strat_rows = search(token, """
        SELECT accessible_bidding_strategy.resource_name,
               accessible_bidding_strategy.name,
               accessible_bidding_strategy.target_cpa.target_cpa_micros
        FROM accessible_bidding_strategy
    """)
    strategy_info = {}
    for row in strat_rows:
        s = row.get("accessibleBiddingStrategy", {})
        rn = s.get("resourceName", "")
        if rn:
            strategy_info[rn] = {
                "name": s.get("name", ""),
                "target_cpa_micros": (s.get("targetCpa") or {}).get("targetCpaMicros", 0) or 0,
            }

    # Fetch change events
    events = search(token, f"""
        SELECT change_event.change_date_time, change_event.changed_fields,
               change_event.change_resource_type, change_event.new_resource,
               change_event.old_resource, change_event.change_resource_name
        FROM change_event
        WHERE change_event.change_date_time >= '{since}'
          AND change_event.change_date_time <= '{until} 23:59:59'
        ORDER BY change_event.change_date_time DESC
        LIMIT 1000
    """)

    bid_changes = {}

    def record(campaign_name, change_type, change_date_str, detail):
        if not campaign_name:
            return
        try:
            dt = datetime.strptime(change_date_str[:19], "%Y-%m-%d %H:%M:%S")
            days_since = max(0, (datetime.utcnow() - dt).days)
        except Exception:
            days_since = 0
        existing = bid_changes.get(campaign_name)
        if existing and existing["change_date"] >= change_date_str[:10]:
            return
        bid_changes[campaign_name] = {
            "has_recent_bid_change": True,
            "change_type": change_type,
            "change_date": change_date_str[:10],
            "days_since": days_since,
            "detail": detail,
        }

    for row in events:
        evt = row.get("changeEvent", {})
        rtype = evt.get("changeResourceType", "")
        change_date = evt.get("changeDateTime", "")
        changed_fields = [f.strip() for f in str(evt.get("changedFields") or "").split(",") if f.strip()]
        fields_str = " ".join(changed_fields)
        resource_name = evt.get("changeResourceName", "")

        if rtype == "CAMPAIGN":
            campaign_name = resource_to_name.get(resource_name, "")
            if not campaign_name:
                campaign_name = (
                    ((evt.get("newResource") or {}).get("campaign") or {}).get("name", "")
                    or ((evt.get("oldResource") or {}).get("campaign") or {}).get("name", "")
                )

            if "bidding_strategy" in fields_str:
                old_s = ((evt.get("oldResource") or {}).get("campaign") or {}).get("biddingStrategy", "")
                new_s = ((evt.get("newResource") or {}).get("campaign") or {}).get("biddingStrategy", "")
                old_num = extract_portfolio_num(strategy_info.get(old_s, {}).get("name", ""))
                new_num = extract_portfolio_num(strategy_info.get(new_s, {}).get("name", ""))
                if old_num is not None and new_num is not None and new_num < old_num:
                    record(campaign_name, "portfolio_upgrade", change_date,
                           f"Portfolio {old_num} → {new_num}")

            if "name" in changed_fields:
                old_name = ((evt.get("oldResource") or {}).get("campaign") or {}).get("name", "")
                new_name = ((evt.get("newResource") or {}).get("campaign") or {}).get("name", "")
                old_num = extract_portfolio_num(old_name)
                new_num = extract_portfolio_num(new_name)
                if old_num is not None and new_num is not None and new_num < old_num:
                    record(new_name or campaign_name, "portfolio_upgrade", change_date,
                           f"Portfolio {old_num} → {new_num} (renamed)")

        elif rtype == "BIDDING_STRATEGY":
            if "target_cpa" in fields_str:
                old_cpa = (((evt.get("oldResource") or {}).get("biddingStrategy") or {}).get("targetCpa") or {}).get("targetCpaMicros", 0) or 0
                new_cpa = (((evt.get("newResource") or {}).get("biddingStrategy") or {}).get("targetCpa") or {}).get("targetCpaMicros", 0) or 0
                if new_cpa and old_cpa and new_cpa > old_cpa:
                    old_usd = old_cpa / 1_000_000
                    new_usd = new_cpa / 1_000_000
                    for cam_name in strategy_to_campaigns.get(resource_name, []):
                        record(cam_name, "tcpa_increase", change_date,
                               f"tCPA ${old_usd:.0f} → ${new_usd:.0f}")

    output = json.dumps(bid_changes, indent=2)
    with open("bid-changes.json", "w") as f:
        f.write(output)

    print(f"Done — {len(bid_changes)} campaigns with recent bid changes")


if __name__ == "__main__":
    main()
