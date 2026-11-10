# Second look: instructions

These questions are open: none has resolved. Do not look anything up and do not try to find out how any of them will turn out.

You are given two file paths: a packet and an output file. The packet is a JSON list of questions (the number varies). Each has:
- `id`, `question`, `resolution_criteria`, `topic`, `resolves_on`
- `digest`: all the research that is available today
- `first_forecast`: a probability and rationale from a forecaster who did NOT see any market price
- `market_price`: the prediction market's probability of yes when today's questions were selected, a few hours ago and before the first forecast was made

For each question, decide the final probability of yes as of today.

How to think, one question at a time:
1. Read the question and the resolution criteria exactly: what counts, by when, and the edge cases.
2. Read the digest. Establish the state of the world today: is the outcome already true or mostly true? How many days remain? Which scheduled events fall before the deadline? Which base rate applies?
3. Compare `first_forecast` with `market_price`. If they are close, make your own best estimate. If they differ a lot, ask what the market is likely weighing that the first forecast missed or underweighted (for example a running total that is already most of the way there, a state that already holds with few days left, consensus expectations for a scheduled decision, the base rate of the status quo persisting). Ask equally whether the digest holds a specific, dated fact that contradicts the market's price.
4. Liquid prediction markets are usually well calibrated; thin ones less so. A large gap most often means the first forecast missed something, but not always. Move away from the market price only for a concrete reason you can name from the digest. Do not split the difference by habit, and do not copy the market by habit either.
5. Keep every probability within 0.02 and 0.98.

Hard rules:
- Use ONLY the packet file you are given. Do not open, list or search any other file or directory anywhere (no repository, no sibling folders, no parent folders).
- Do not search or fetch the web.
- Do not use any memory of how a question turned out. If you believe you know an outcome, ignore it and say so in that question's note.
- Think about each question individually. Do not apply one formula to all of them.

Output: write the output file you are given as
`{"<id>": {"probability": <number>, "note": "<one or two sentences: what decided it; if you end more than 10 points from the market price, name the specific fact from the digest>"}, ...}`
with every id in the packet and no others. Validate that it parses as JSON and has exactly the packet's ids.

Report back in under 60 words: how many forecasts you wrote, and the full list of files you read. No per-question detail.
