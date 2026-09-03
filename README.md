# Single Bougie Free — AWS Cloud Portfolio

> *"This is what I built without a cloud job. Imagine what I could build with one."*

**[singlebougiefree.com](https://singlebougiefree.com)** · Built by [Katurah Bobo](mailto:bobokatur.tech@gmail.com) · Aspiring Cloud Solutions Architect | FinOps Focus

---

## What This Is

Single Bougie Free (SBF) is a wellness community platform for women embracing singlehood — and a fully production AWS architecture I designed, built, and deployed solo.

This isn't a tutorial clone. It's a real platform with real infrastructure decisions, real cost tradeoffs, and real users. Every service was chosen intentionally. Every decision I'm ready to defend.

**The dual purpose:** Build something meaningful for women who look like me, while proving I can architect cloud systems before anyone gives me the title to do it.

---

## Live Architecture

```
User → Route 53 → CloudFront (WAF + OAC) → S3 (Static Frontend)
                                          ↓
                              API Gateway → Lambda → DynamoDB
                                                   → SES (Email)
                                                   → SNS (Alerts)
                                          ↓
                              EventBridge → Batch Lambda → S3 (Newsletters)
                                                         → SES (Monthly Send)
```

---

## Production Services

| Service | Role | Why This, Not That |
|---|---|---|
| **S3** | Static hosting + newsletter storage | Serverless, versioned, SSE-S3 encrypted. No EC2 idle cost. |
| **CloudFront** | CDN + security layer | OAC (not OAI), WAF in block mode, ACM SSL. Industry standard for secure static delivery. |
| **Route 53** | DNS + domain routing | A alias records for apex + www. Namecheap NS pointed here. |
| **Lambda** | All backend logic | Scales to zero between submissions. Pay per request, not per hour. |
| **API Gateway** | HTTP entry points | `/prod/intake` (newsletter signup), `/prod/contact` (community submissions) |
| **DynamoDB** | Member data store | Schemaless, read-heavy member records. On-demand billing — no running DB instance cost. |
| **SES** | Transactional + batch email | Welcome emails on signup. Monthly newsletter delivery. DKIM verified on `singlebougiefree.com`. |
| **SNS** | Error alerting | `sbf-newsletter-errors` topic → admin email on Lambda failure. |
| **EventBridge** | Scheduled automation | Cron rule fires newsletter Lambda on the 1st of every month. Zero manual intervention. |
| **Secrets Manager** | Token security | HMAC-signed unsubscribe tokens. Secret cached in Lambda to reduce API calls. |

---

## Lambda Functions

| Function | Trigger | What It Does |
|---|---|---|
| `sbf-intake-handler` | POST `/intake` | Validates form, writes to DynamoDB, sends SES welcome email with current newsletter embedded |
| `sbf-contact-form` | POST `/contact` | Saves community submissions to DynamoDB, notifies admin via SES |
| `sbf-newsletter-generator` | Airtable → EventBridge | Pulls curated content, generates HTML newsletter, writes to S3 + metadata JSON |
| `sbf-monthly-batch-sender` | EventBridge (1st of month) | Scans IntakeSubmissions, sends newsletter to all opted-in members, updates `emailsSentCount` |
| `sbf-unsubscribe-handler` | GET (token link) | Validates HMAC token, updates DynamoDB opt-out status, renders confirmation page |
| `sbf-archive-index` | S3 event | Reads newsletter metadata from S3, builds archive index for The Tea Vault page |

---

## FinOps Decisions

Cost awareness is built into every architecture choice here — this isn't an afterthought.

- **Lambda over EC2** — No idle compute. SBF traffic is event-driven (form submissions, monthly sends). EC2 would run 24/7 for bursty workloads.
- **DynamoDB on-demand over RDS** — Member data is simple key-value. On-demand = pay per read/write, not per hour for a running database instance.
- **S3 for newsletter storage** — Writing metadata to S3 at generation time eliminated a redundant Airtable-query Lambda at page load. Cheaper, faster, simpler.
- **CloudFront invalidations** — Free. Cost comes from data transfer + request counts. Lambda@Edge avoided intentionally (adds cost per request).
- **Secrets Manager caching** — Secret fetched once per Lambda container lifecycle, not per invocation. Reduces API calls and cost.

---

## Email Pipeline

```
User signs up (free.html intake form)
    ↓
API Gateway → sbf-intake-handler Lambda
    ↓
DynamoDB write (always executes first)
    ↓
SES welcome email (non-blocking — record saves even if email fails)
    ↓                                    
IntakeSubmissions table updated

Monthly (EventBridge cron, 1st of month):
    sbf-monthly-batch-sender → scans all optedIn=true records → SES batch send
```

**DKIM verified** on `singlebougiefree.com` · Unsubscribe flow: HMAC-signed tokens, 7-day expiry, two-step confirmation

---

## Content Pipeline (Airtable → Newsletter → S3)

Airtable serves as a no-code CMS for newsletter curation — 6 content sections, 22 fields per monthly issue. This keeps engineering effort focused on infrastructure, not building an admin UI.

```
Airtable (monthly curation) → sbf-newsletter-generator Lambda
    ↓
newsletters/{month}-newsletter.html → sbf-newsletter S3 bucket
metadata/{month}-metadata.json     → sbf-newsletter S3 bucket
    ↓
sbf-archive-index Lambda → The Tea Vault (archive.html) card grid
```

---

## Site Structure

| Page | Purpose |
|---|---|
| `index.html` | Landing — hero, bio, architecture overview |
| `single.html` | Community pillar — legal, housing, relationships |
| `bougie.html` | Community pillar — self-care, wellness, luxury on a budget |
| `free.html` | Community pillar — finances, independence, newsletter signup |
| `archive.html` | The Tea Vault — newsletter archive (Tier 3 in progress) |
| `tech.html` | AWS portfolio page — architecture decisions, roadmap |

---

## Roadmap — In Progress

- [ ] **CodePipeline** — GitHub → S3 deploy → CloudFront auto-invalidation (replacing manual deploys)
- [ ] **Visitor counter** — Lambda + DynamoDB counter replacing broken third-party badge
- [ ] **Tea Vault (Tier 3)** — `archive.html` live card grid pulling from S3 metadata
- [ ] **Synthetic data** — ~500 Faker records for IntakeSubmissions pipeline testing
- [ ] **QuickSight dashboards** — Cost Explorer → Athena → QuickSight (user trends + FinOps view)

---

## Roadmap — The K-Vault (Conceptual Architecture)

*Designed but not yet deployed. This is the architecture SBF would require at scale and monetization. Every service chosen intentionally.*

**The K-Vault — Premium Portal**
- Freemium model: Free / $9.99 monthly / $99 annual
- Auth: Cognito (Google + Apple SSO) → Stripe webhook → Lambda → Cognito group update
- Resource gating: "Go Deeper" links resolve by Cognito tier. Free users see the upgrade prompt. One template to maintain.
- Digital products: Templates, workbooks, mini courses — one-time Stripe purchases alongside subscription

**Real-time Mood Pipeline**
```
Mood surveys → Kinesis → Apache Flink (anomaly detection) → Lambda → SNS push notification
```

**Auto Baddie Promotions**
```
EventBridge (monthly) → Lambda → recalculate designations → DynamoDB update → SNS congratulations
```
Member tiers: New → Freshman → Sophomore → Junior → Senior Baddie

**Community Analytics**
```
Mood data + engagement → Athena (SQL queries) → DataBrew (scheduled cleaning) → QuickSight dashboards
```
Three views: member personal trends / admin aggregate / FinOps cost visibility

**Resource Hub**
```
AWS Data Exchange datasets → EventBridge → DataBrew pipeline → curated resource library
```

---

## About the Builder

**Katurah Bobo** — Claims adjuster for 4 years. Aspiring Cloud Solutions Architect with a FinOps focus. Currently enrolled in CIST 2484 AWS Cloud Operations at Georgia Piedmont Technical College (Summer 2026), mapping every course module directly to this production architecture.

Fighting the "need experience to get experience" cycle by building the proof myself.

- 🌐 [singlebougiefree.com](https://singlebougiefree.com)
- 💼 [LinkedIn](https://www.linkedin.com/in/katurah-b-62464062)
- 📧 bobokatur.tech@gmail.com

> *"I'm looking for a leader who invests in potential — the kind who looks back and says they saw it first."*

---

*Single Bougie Free · Production AWS Platform · Built solo, 2025–2026*
