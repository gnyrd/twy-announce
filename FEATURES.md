# twy-announce-poster - Strategic Roadmap

**Last Updated:** 2025-11-20

---

## 🎯 Planned Features

### Cloud Deployment
**Timeline:** Phase 2 (after local version working)  
**Estimated Effort:** Medium (4-6 hours)  
**Dependencies:** Working local system

**Description:**
Move from local Mac deployment to cloud-based scheduling for better reliability.

**Options:**
- Google Cloud Functions + Cloud Scheduler
- AWS Lambda + EventBridge
- Keep WhatsApp session management working in serverless

**Benefits:**
- No need for Mac to be on 24/7
- More reliable scheduling
- Better logging and monitoring
- Easier to scale if needed

---

### Multi-Group Support
**Timeline:** Future  
**Estimated Effort:** Small (2-3 hours)  
**Dependencies:** Single group working reliably

**Description:**
Support posting to multiple WhatsApp groups with different schedules.

**Use cases:**
- Different groups for different event types
- Backup/redundancy groups
- Testing groups

---

### Web Dashboard
**Timeline:** Future  
**Estimated Effort:** Large (8-12 hours)  
**Dependencies:** Core system stable

**Description:**
Simple web interface for:
- Viewing upcoming scheduled posts
- Manual triggering of posts
- Viewing post history
- Editing message templates
- System health monitoring

**Tech stack:** Flask or FastAPI with simple HTML/JS frontend

---

### Advanced Scheduling Rules
**Timeline:** Future  
**Estimated Effort:** Medium (4-5 hours)  
**Dependencies:** Basic scheduling working

**Description:**
More sophisticated scheduling beyond "day before":
- Multiple reminders (week before, day before, hour before)
- Different posting times for weekday vs weekend events
- Skip certain event types
- Custom rules per event category

---

## 💡 Future Ideas

### Monitoring & Alerts

**Email/SMS Notifications**
- Alert if posting fails
- Daily success/failure summary
- Warning if Google Drive document hasn't been updated recently

**Health Monitoring**
- Track successful post rate
- Monitor WhatsApp connection status
- Alert if system hasn't run expected job

**Logging Improvements**
- Structured logging (JSON)
- Log aggregation service integration
- Better debugging for failures

---

### Google Drive Enhancements

**Format Flexibility**
- Support multiple document formats (Google Docs, Sheets, etc.)
- Better handling of format changes
- Visual format validation tool

**Collaborative Editing**
- Handle document changes while system is reading
- Version detection to avoid posting stale data
- Conflict resolution

**Caching**
- Cache parsed events to reduce API calls
- Detect document changes efficiently
- Faster startup time

---

### Platform Integrations

**Alternative to Marvelous**
- Modular design for easy platform swapping
- Support multiple platforms simultaneously
- Platform health checking

**Additional Data Sources**
- Import from Google Calendar
- Support for iCal feeds
- Manual event addition via API

---

### Message Improvements

**Rich Formatting**
- Support for images in posts
- Formatted text (bold, italic, lists)
- Emojis and reactions

**Personalization**
- Different messages for different audience segments
- A/B testing message templates
- User engagement tracking

**Message Validation**
- Preview mode before posting
- Spell checking
- Link validation

---

### Reliability & Recovery

**Retry Logic**
- Intelligent retry on failures
- Exponential backoff
- Max retry limits

**Fallback Mechanisms**
- Fallback to email if WhatsApp fails
- Backup posting methods
- Manual intervention notifications

**State Management**
- Track what's been posted
- Prevent duplicate posts
- Handle system restarts gracefully

---

### Testing & Development

**Better Testing**
- Mock WhatsApp for unit tests
- Fake Google Drive data for testing
- Automated integration tests
- Load testing for scheduler

**Development Tools**
- Local development mode
- Test message generator
- Configuration validator
- Dry-run improvements

---

## 🔮 Long-Term Vision

### Full Event Management System
Transform from simple poster to complete event management:
- Event RSVP tracking
- Attendance tracking
- Post-event feedback collection
- Analytics and reporting

### Multi-Platform Posting
Expand beyond WhatsApp:
- Telegram groups
- Discord servers
- Slack channels
- Social media (Twitter, Facebook)

### AI Integration
Intelligent message generation:
- Summarize event details automatically
- Generate engaging copy
- Optimize posting times based on engagement
- Sentiment analysis of responses

---

## 🚫 Explicitly Not Planned

### Two-Way Communication
**Reason:** Scope creep. This is a posting system, not a chat bot.
- Won't handle responses from group members
- Won't answer questions automatically
- Won't process commands in group chat

**If needed later:** Build as separate component/project

---

### Event Management in Drive Document
**Reason:** Google Drive is the source of truth. We just read it.
- Won't edit the Drive document programmatically
- Won't add/remove events
- Won't mark events as "posted"

**Rationale:** Keep clear separation of concerns. If this is needed, the source document should move to a database.

---

### Payment Processing
**Reason:** Out of scope for admin posting system.
- Won't handle class payments
- Won't integrate with payment processors
- Won't track financial information

**Rationale:** Marvelous platform handles this. Keep systems separate.

---

### Group Management
**Reason:** WhatsApp handles this fine.
- Won't add/remove group members
- Won't change group settings
- Won't moderate content

**Rationale:** Admin functions should stay with human admins. Automation is just for posting.

---

## 📊 Feature Prioritization Criteria

When evaluating new features:

1. **Does it improve reliability?** → High priority
2. **Does it reduce manual work?** → Medium-High priority  
3. **Does it prevent posting failures?** → High priority
4. **Does it require significant new dependencies?** → Lower priority
5. **Does it complicate the core posting flow?** → Avoid
6. **Does it require ongoing maintenance?** → Carefully consider

**Philosophy:** Keep the system simple and reliable. Admin posting is critical, so features should make it MORE reliable, not more complex.

---

## 🔄 Feature Request Process

1. Add idea to "Future Ideas" section
2. Evaluate against prioritization criteria
3. If valuable, move to "Planned Features" with timeline
4. When starting work, create task in TASKS.md
5. When complete, document in HISTORY.md

---

## Questions to Answer

**For Cloud Deployment:**
- What's the cold start time for serverless WhatsApp session?
- Can we persist WhatsApp session across function invocations?
- Cost comparison: local Mac vs. cloud

**For Marvelous Integration:**
- Do they have plans for an official API?
- How stable is their website structure for scraping?
- What's the failure rate we should plan for?

**For Multi-Group:**
- Are there other groups that need this?
- Different posting schedules per group?
- Shared or separate configurations?
