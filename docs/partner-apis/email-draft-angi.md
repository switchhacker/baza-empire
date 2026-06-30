# Application email draft — Angi Leads (lead feed integration)

**Send from:** your business address (contactahbco@gmail.com or serge@ahb123.com)
**To:** crmintegrations@angi.com
**Subject:** Angi Leads JSON lead-feed integration — All Home Building Co LLC

**Before you send — fill these in (marked `[ ]` below):**
- `[SPID / Service Provider ID]` — on your Angi agreement/bill and in your Angi-for-Pros URL.
- `[Angi Leads account number]`.
- `[your name / title / phone]`.
- `[production endpoint URL]` and `[test endpoint URL]` — these need baza's stable public HTTPS URL (the DDNS/Caddy fix). If that isn't ready, leave the line that says "endpoints to follow" and remove the URLs — they can be provided once the integration is approved to proceed.

---

Hello Angi Integrations team,

I'm writing to set up a **JSON lead-feed integration** for our Angi Leads account so our incoming leads are delivered directly into the system we use to run our business.

**Business details**
- Legal name: All Home Building Co LLC
- Service Provider ID (SPID): [SPID]
- Angi Leads account number: [Angi Leads account number]
- Associated email: contactahbco@gmail.com
- Contact: [your name], [title], [phone]

**What we're asking for**
We run our own in-house operations system and would like to receive our Angi leads via your JSON lead feed (HTTP POST to an endpoint we host). We are a single first-party business integrating our **own** leads — not a CRM vendor or a lead reseller. Could you confirm whether a direct lead feed to our own endpoint is available to us, and send over the **field-definition document** (the JSON schema) so we can configure our endpoint to match?

**On our side, we can support your documented contract:**
- A secure HTTPS endpoint that accepts your JSON lead payloads.
- Authentication via an `X-API-KEY` header (we'll provide the key).
- A JSON response containing `"success"` to acknowledge each delivered lead.
- Separate **test** and **production** endpoints.

Endpoints:
- Test: [test endpoint URL]
- Production: [production endpoint URL]
*(or: "We'll provide the test and production endpoint URLs once you confirm the integration can proceed.")*

If this needs to route through a partner CRM rather than a direct feed, please let me know what that path looks like.

Thank you — happy to provide anything else you need to get this set up.

Best regards,
[your name]
All Home Building Co LLC
ahb123.com · contactahbco@gmail.com · [phone]

---

*Notes for context (do not send): Angi delivers leads as a server-to-server JSON POST with an `X-API-KEY` header and expects a `"success"` JSON ack; field set is fixed/non-customizable. Angi has no official reviews or profile API — this request is leads only (reviews stay on email-parse). Source: intercom.help/angi API-integration setup; corroborated by a contractor who received Angi's "NEW JSON Lead Feed" doc.*
