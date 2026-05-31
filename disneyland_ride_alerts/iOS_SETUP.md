# Running the alerts on an iPhone (no computer, no cloud)

iOS won't keep the Python service (`ride_alerts.py`) alive in the background —
the OS suspends and kills background processes. The phone-only, cloud-free way
to get the same alerts is with **Apple Shortcuts + Personal Automations**, which
hit the same [queue-times.com](https://queue-times.com) API and fire a
**local notification**.

> **Why notifications instead of email?** On iOS the Shortcuts "Send Email"
> action opens the Mail compose sheet and needs a manual *Send* tap — it can't
> send hands-free. Truly automatic *email* requires a server (i.e. cloud), which
> you've ruled out. A local notification is tap-free and just as loud, so we use
> that. (You can still add an optional email step that you tap to send.)

---

## Part A — Build the Shortcut ("Disney Ride Check")

Open the **Shortcuts** app → **+** (new shortcut) → name it `Disney Ride Check`.
Add these actions in order. Search the action name in the bottom search bar.

### 1. Get the live data
- **Get Contents of URL**
  - URL: `https://queue-times.com/parks/16/queue_times.json`
  - Method: `GET`
  - (Park id `16` = Disneyland Park, California.)

The output of this action is the variable **Contents of URL** — we'll match
text against it. The API always orders each ride as
`"name":...,"is_open":true/false,"wait_time":N`, so a regex right after the
ride's name reliably grabs both values.

### 2. Parse "Rise of the Resistance"
- **Match Text**
  - Text: `Contents of URL`
  - Regular Expression:
    ```
    Rise of the Resistance","is_open":(\w+),"wait_time":(\d+)
    ```
- **Get Group at Index** → Index `1` → from the Match Text result.
  Rename this magic variable to **RotR_Open** (`true` or `false`).
- **Get Group at Index** → Index `2` → from the Match Text result.
  Rename to **RotR_Wait** (a number, in minutes).

### 3. Decide whether to alert (Rise of the Resistance)
- **If** `RotR_Open` `is` `true`
  - **If** `RotR_Wait` `is less than` `30`
    - **Show Notification**:
      `🎢 Rise of the Resistance is OPEN — only [RotR_Wait] min! Go now.`
  - **Otherwise** *(optional — “it’s open” ping; can be noisy, see Part C)*
    - **Show Notification**:
      `Rise of the Resistance is OPEN ([RotR_Wait] min).`
  - **End If**
- **End If**

### 4. Repeat for "Smugglers Run"
Duplicate the three blocks above, but use this regex in **Match Text**:
```
Smugglers Run","is_open":(\w+),"wait_time":(\d+)
```
Name the groups **Smugglers_Open** / **Smugglers_Wait** and adjust the
notification text. (Tip: long-press the actions → *Duplicate* to copy the block,
then just swap the regex and variable names.)

You can tap ▶ at the bottom to test it right now — it should run with no errors
and, if a ride is open and under 30 min, pop a notification.

---

## Part B — Schedule it through the day

iOS time automations fire at a **specific time, repeating Daily** — there's no
built-in "every 30 minutes." So create several automations that all run the same
shortcut. ~10–14 of them covers park hours.

For **each** time slot:

1. Shortcuts app → **Automation** tab → **+** → **Create Personal Automation**.
2. Choose **Time of Day** → pick a time (e.g. `8:00 AM`) → **Repeat: Daily**.
3. Action: **Run Shortcut** → `Disney Ride Check`.
4. **Turn OFF "Ask Before Running"** and confirm **Run Immediately** — this is
   what lets it run silently in the background.

Suggested slots (Disneyland typically opens 8 AM): `8:00, 8:30, 9:00, 9:30,
10:00, 11:00, 12:00, 1:00 PM, 3:00, 5:00, 7:00, 9:00`. Add/trim to taste —
more slots = faster alerts but more battery.

> There are also free third-party apps (e.g. shortcut-runner / "recurring
> automation" utilities) that trigger a shortcut every N minutes from one
> automation, if hand-making a dozen feels tedious. Those run on-device, no cloud.

---

## Part C — Avoid duplicate pings (optional, advanced)

Without memory, the "it's open" notification (Part A step 3 *Otherwise*) will
fire on **every** poll while the ride is open. Two ways to tame it:

- **Simple (recommended):** delete the *Otherwise* branch and only notify when a
  ride is **open AND under 30 min**. That's the actionable case anyway, and it
  only pings you when it's genuinely worth walking over. You'll already know the
  rides "opened" because the park opens ~8 AM.
- **With memory (advanced):** store state in a local file so each alert fires
  once. In the shortcut, use **Get File** / **Save File** pointed at
  *On My iPhone* (local, not iCloud) to read/write a small text file like
  `ride_state.txt`, and only notify when today's state changes. This is more
  actions but gives you a clean one-ping-per-event behavior, including a distinct
  "just opened" alert.

---

## Reliability expectations

- Automations only run while the **phone is on and has network** (Wi-Fi or
  cellular). They survive a locked screen but not a powered-off phone.
- "Run Immediately" automations are background-friendly, but iOS may delay one by
  a minute or two under heavy load — fine for ride waits.
- Keep the phone charged during park hours; polling + screen wakes use battery.
- This is "good enough for a day at the park." For rock-solid 24/7 monitoring,
  the Python service (`ride_alerts.py --loop`) on any always-on machine is more
  reliable — but the Shortcut is the best you can do phone-only with no cloud.

---

## Quick reference

| Thing | Value |
|---|---|
| API URL | `https://queue-times.com/parks/16/queue_times.json` |
| Park | Disneyland Park, California (id `16`) |
| RotR regex | `Rise of the Resistance","is_open":(\w+),"wait_time":(\d+)` |
| Smugglers regex | `Smugglers Run","is_open":(\w+),"wait_time":(\d+)` |
| Group 1 | `is_open` (`true`/`false`) |
| Group 2 | `wait_time` (minutes) |
| Alert rule | open **and** wait `< 30` |
