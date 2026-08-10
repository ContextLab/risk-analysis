# Data-use permission request — Dominating 12

**Status:** **SENT 2026-08-10** by Jeremy via the D12 contact form. Awaiting reply.

Follow-up date if no response: **2026-08-24** (~2 weeks). Log any reply below, dated.

**Why this exists:** the D12 Terms of Use (`/legal/terms`, rev. 2009-12-05) prohibit scraping
"without the Company's prior written permission," and `robots.txt` disallows `/game/`, `/user/`,
and `/userlist`. Written permission is the remedy the ToS itself names, so this letter is the
unblocker for the observational half of issue #1.

**How to send:** https://dominating12.com/contact — form fields are Name, Email, Subject
(dropdown), Body. Choose subject **"Other"**. There is no published contact email address; the
form is the only channel. They also ask that general suggestions go to the forums, but a data-use
request is correspondence, not a suggestion, so the form is right.

**If no reply in ~2 weeks:** consider a polite follow-up via the forums, or proceed with the
simulator-only track (which is being built in parallel and does not depend on this).

---

## Form fields

**Name:** Jeremy R. Manning

**Email:** jeremy.r.manning@dartmouth.edu

**Subject:** Other

**Body:**

```
Hello,

I'm a faculty member at Dartmouth College, where I direct the Contextual Dynamics Lab
(https://www.context-lab.com). I'm also a Dominating 12 player (username: jrm).
I'm writing to ask for written permission to use D12 data in a non-commercial academic
research project, since the Terms of Use reserve that permission to you.

WHAT WE'D LIKE TO STUDY

We study how groups of people coordinate, negotiate, and change strategy over time. D12 is
an unusually good setting for this: games are long, the board state is fully observable and
quantifiable, and players talk to each other while they play. We'd like to ask questions like:

  - Do different maps produce recognizably different styles of game?
  - Can we predict a player's next move from the board position alone, and does adding what
    they said improve that prediction?
  - Do negotiations follow stereotyped arcs, and do those arcs depend on whether a player is
    winning or losing at the time?

To our knowledge nobody has looked at strategic negotiation at this scale with this much
board-state detail available alongside it.

WHAT WE'D NEED

  - Map definitions (territories, adjacencies, continents/bonuses) from /maps
  - Game logs (moves, turn order, outcomes)
  - In-game chat associated with those games
  - Basic public profile/ranking data for the players involved

If it's easier on your end to send us a database export or point us at an API than to have us
retrieve pages, we'd much prefer that — it's less load on your servers and cleaner data for us.

HOW WE'D HANDLE IT

  - Non-commercial academic research only. No advertising, no resale, no derived product.
  - If we do retrieve pages, we'd do it slowly (on the order of one request every few seconds),
    once, with caching, identifying ourselves in the User-Agent, at whatever rate you specify.
  - Usernames pseudonymized in any publication. No real names, no email addresses, no attempt
    to deanonymize.
  - We would not republish your maps, artwork, or bulk site content. Figures would show data we
    derive (e.g. territory adjacency graphs), not your graphics.
  - Quoted chat, if any, only in short excerpts and only with the associated account
    pseudonymized — or omitted entirely if you'd rather.
  - We're glad to restrict the whole thing to players who opt in, if you'd prefer that. We can
    post a request in the forums and only include people who say yes.

WHAT WE'D GIVE BACK

Any code we write would be released open-source, and the paper would be freely available. We'd
credit Dominating 12 as the data source (or keep the site unnamed if you'd rather). We're also
happy to share results with the community — player-facing summaries, map-balance statistics,
or anything else that looks useful. If there's an analysis you've wondered about yourselves,
we'd take requests.

We'd of course sign whatever data-use agreement you'd want, and we're happy to get on a call.

If the answer is no, that's completely fine — just let us know and we won't proceed.

Thank you for your time, and for running the site.

Best,
Jeremy Manning
Associate Professor of Psychological and Brain Sciences
Dartmouth College
www.context-lab.com
```

---

## Notes for us

- Keep a dated copy of any reply in this folder. Permission scope determines what the scraper
  is allowed to touch, and the scraper should refuse to fetch anything outside it.
- If permission arrives, the granted terms (rate limit, allowed paths, retention, publication
  constraints) get encoded as config in the package, not as informal practice.
- If permission is denied or never arrives, the simulator track stands on its own.
