"""Out-of-band delivery of the notifications the application generates.

Notifications are **generated** inside the application as in-app inbox rows and
**delivered** afterwards by a dispatcher that runs out of band. The two halves
are deliberately separate:

* generation writes an in-app row and one pending delivery record per
  configured destination - it never opens a socket;
* delivery happens later (operator command or administrator-triggered run)
  and may fail without touching the assessment, the approval or the decision
  that produced the notification.

A delivery is a message *about* an event. It never authorizes, approves or
changes anything, and it never carries assessment content, a person, a token
or a credential.
"""

from __future__ import annotations
