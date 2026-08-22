import { useEffect, useState } from "react";

import { ApiError, getCaseAudit, isAbortError, type AuditEvent } from "../api/client";
import { formatDateTime } from "../presentation";

export function AuditTimeline({ caseId, refreshKey = 0 }: { caseId: string; refreshKey?: number }) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void getCaseAudit(caseId, { signal: controller.signal })
      .then((rows) => {
        setEvents(rows);
        setError(null);
      })
      .catch((requestError: unknown) => {
        if (!isAbortError(requestError)) {
          setError(requestError instanceof ApiError ? requestError.message : "The audit timeline is unavailable.");
        }
      });
    return () => controller.abort();
  }, [caseId, refreshKey]);

  return (
    <section aria-labelledby="audit-title" className="panel audit-timeline">
      <h2 id="audit-title">Decision audit</h2>
      {error ? <p className="notice notice--error" role="alert">{error}</p> : null}
      {events.length === 0 && error === null ? <p>No audit events recorded.</p> : null}
      <ol>
        {events.map((event) => (
          <li key={event.event_id}>
            <strong>{event.event_type.replaceAll("_", " ")}</strong>
            <span>{formatDateTime(event.occurred_at)} · {event.actor_id}</span>
            {event.justification ? <p>Justification: {event.justification}</p> : null}
            {event.reason ? <p>Reason: {event.reason}</p> : null}
          </li>
        ))}
      </ol>
    </section>
  );
}
