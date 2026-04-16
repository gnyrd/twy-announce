# 🧭 Tiffany Wood Yoga  
### Mailchimp + Hey Marvelous Data Architecture & Naming Strategy  
*(December 2025 Edition)*

---

## 1️⃣ Core Principles

- **Single Source of Truth:**  
  Hey Marvelous holds *purchase and membership data* (who’s actually a paying member).  
  Mailchimp holds *communication data* (who you talk to and how).  
  → Mailchimp mirrors Marvelous, never the other way around.  

- **One Master Audience:**  
  All subscribers, students, and alumni remain in one Mailchimp audience.  
  Use **Tags** and **Segments** for differentiation — never multiple audiences.  

- **Automate Whenever Possible:**  
  Hey Marvelous → Mailchimp sync (via native integration or Zapier) automatically applies or removes tags based on user activity.

---

## 2️⃣ Tag Architecture

Tags = static labels that describe **who someone is** or **what they’ve done**.

### 2.1 Tag Naming Convention
`[Purpose] - [Date or Cycle] - [Offering or Context]`

**Examples**
- Blitz - Dec 2025 - Yoga Lifestyle  
- Blitz - Dec 2025 - Yoga Lifestyle - Responded  
- Blitz - March 2026 - Retreat Alumni  
- Retreat - Mexico 2025  
- Membership - Yoga Lifestyle  
- Master Class - Kali 2025  
- Region - Utah  
- Status - Member
- Status - Yoga Lifestyle - Canceled
- Status - TWY Archive - Canceled
- Lifecycle - Alumni
- Lifecycle - VIP
- Source - Website Footer

### 2.2 Tag Categories

| Category       | Purpose                              | Example                                       |
| -------------- | ------------------------------------ | --------------------------------------------- |
| **Status**     | Relationship or account stage        | Status - Member, Status - Lead, Status - Yoga Lifestyle - Canceled |
| **Offering**   | What they purchased or attended      | Retreat - Mexico 2025                         |
| **Membership** | Current membership plan              | Membership - Yoga Lifestyle                   |
| **Region**     | Geographic relevance                 | Region - Utah                                 |
| **Lifecycle**  | Long-term relationship label         | Lifecycle - Alumni                            |
| **Blitz**      | Tracks who received a marketing send | Blitz - Dec 2025 - Yoga Lifestyle             |
| **Responded**  | Tracks those who converted           | Blitz - Dec 2025 - Yoga Lifestyle - Responded |
| **Source**     | Acquisition channel / signup origin  | Source - Website Footer                       |

### 2.3 Tag Governance
- Use consistent capitalization and hyphen spacing: `Category - Subcategory - Detail`.  
- Archive expired event or promo tags quarterly under one umbrella tag: `Archive - 2025`.  
- Avoid duplication (e.g., don’t keep both Utah and Utah - Local 2021).

---

## 3️⃣ Segment Architecture

Segments = dynamic filters that define **who you want to talk to now**.

### 3.1 Segment Naming Convention
`[Audience or Focus] - [Condition] - [Source or Timing]`

**Examples**
- Lifestyle  
- Retreat Alumni - 2024 Mexico + Palouse  
- Newsletter Subscribers - New This Month  
- Inactive Subscribers - No Opens 6 Months  
- Teachers and Graduates - Continuing Education

### 3.2 Core Dynamic Segments

| Segment Name                                     | Definition                                                | Purpose                           |
| ------------------------------------------------ | --------------------------------------------------------- | --------------------------------- |
| Lifestyle | Tag = Status - Member + Tag = Membership - Yoga Lifestyle | Weekly content to current members |
| Retreat Alumni                                   | Tag contains “Retreat” OR “Mexico” OR “Palouse”           | Reunion + advanced offerings      |
|| Newsletter Only                                  | Tag = Status - Lead AND NOT any Membership - * tags       | Lifestyle content & nurture       |
| Teachers and Graduates                           | Tag contains “Teacher Training” OR “Graduate”             | Continuing education              |
| Inactive Subscribers                             | 0 opens in 6 months                                       | Re-engagement or suppression      |
| New This Month                                   | Signup < 30 days                                          | Welcome / onboarding flow         |
| VIP / High Engagement                            | Open rate > 70% OR Tag Lifecycle - VIP                    | Early access, thank-you offers    |
| Local Students - Utah                            | Tag Region - Utah                                         | Local workshops + events          |

---

## 4️⃣ Synchronization Rules  
*(Hey Marvelous ↔ Mailchimp)*

| Event in Hey Marvelous      | Mailchimp Action | Tag Applied / Removed                            |
| --------------------------- | ---------------- | ------------------------------------------------ |
| New account created         | Add to audience  | Status - Lead                                    |
| Purchases membership        | Update contact   | + Status - Member + Membership - Yoga Lifestyle  |
| Cancels membership          | Update contact   | - Status - Member + Status - *Product* - Canceled<br>**KEEP Membership tag** (historical record)<br>Example: Status - Yoga Lifestyle - Canceled |
| Buys course / retreat       | Add tag          | Retreat - Mexico 2025 (or relevant offering tag) |
| Subscription expires > 6 mo | Update contact   | + Lifecycle - Alumni                             |
| Signs up newsletter only    | Add tag          | Status - Lead                                    |
| Signs up via website footer | Add tag          | Status - Lead + Source - Website Footer          |

Implement via **native Marvelous → Mailchimp integration** plus **Zapier** for membership termination or special cases.

---

## 5️⃣ Campaign Tracking + Auditing

### 5.1 Campaign Tagging Pattern
`Blitz - [Month Year] - [Audience or Offering]`

**Example:**  
Blitz - Dec 2025 - Yoga Lifestyle

### 5.2 Respondent Tag
Applied automatically upon purchase or signup:  
Blitz - Dec 2025 - Yoga Lifestyle - Responded

### 5.3 Quarterly Audit Checklist
- ✅ Archive old blitz tags  
- ✅ Check for duplicates or inconsistent naming  
- ✅ Confirm Marvelous → Mailchimp tag sync  
- ✅ Purge inactive subscribers (per Mailchimp guidelines)  

---

## 6️⃣ Data Governance Practices

| Cadence       | Task                                                     |
| ------------- | -------------------------------------------------------- |
| **Monthly**   | Review recent imports and confirm tags applied correctly |
| **Quarterly** | Audit tag list, merge duplicates, archive old blitz tags |
| **Bi-Annual** | Validate automations and Zapier connections              |
| **Annually**  | Refresh naming conventions doc and team training         |

---

## 7️⃣ Example Workflow: Acquisition Blitz (Dec 2025)

1. Create tag: Blitz - Dec 2025 - Yoga Lifestyle  
2. Export Active Members from Marvelous → import to Mailchimp with that tag.  
3. Send email series to segment Lifestyle.  
4. On purchase: Zapier adds Blitz - Dec 2025 - Yoga Lifestyle - Responded.
5. After campaign:  
   - Compare opens, clicks, and conversions between tagged groups.  
   - Archive the blitz tag after 90 days.

---

## 8️⃣ Quick Reference: Tag vs Segment

| Feature           | Represents                                                   | Updates When                  | Example                                          |
| ----------------- | ------------------------------------------------------------ | ----------------------------- | ------------------------------------------------ |
| **Tag**           | Identity / static label                                      | Only when applied or removed  | Retreat - Mexico 2025                            |
| **Segment**       | Dynamic filter / current state                               | Automatically, based on rules | Lifestyle |
| **Rule of Thumb** | Tag = Who they are / what they’ve done · Segment = Who’s relevant now |                               |                                                  |

---

## 9️⃣ File Maintenance & Team Usage

- Store this document in a shared folder (`/Marketing/Architecture/Mailchimp_Marvelous_Strategy.md`).  
- Update the version and date at the top each time edits occur.  
- When onboarding assistants, review:  
  - Tag categories and naming  
  - Segment creation process  
  - Quarterly audit checklist  

---

### ✨ Final Note
Your Mailchimp is your **living record of relationship**.  
Hey Marvelous tells you who *paid*; Mailchimp tells you who *engages*.  
Keep both in rhythm, and your communication becomes as alive and precise as your teaching.