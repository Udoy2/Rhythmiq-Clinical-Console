import { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { api } from '../api/endpoints';
import { cx } from '../lib/format';

type Msg = { role: 'user' | 'assistant'; content: string; source?: string };
type Suggestion = { id: string; label: string };

const NUDGE_KEY = 'rq.nudge.patients';
const NUDGE_TEXT = 'Need help opening a DEMO patient? I can walk you to Start replay.';

/** The only proactive nudge: once per browser session, on the patient list. */
const nudgeAllowed = (pathname: string) => {
  if (pathname !== '/patients') return false;
  try { return !sessionStorage.getItem(NUDGE_KEY); } catch { return false; }
};

/** Floating product guide — all authenticated pages (not landing). */
export function DrRhidoyChathead() {
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [nudge, setNudge] = useState<string | null>(null);
  const [suggestionsOpen, setSuggestionsOpen] = useState(true);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [messages, setMessages] = useState<Msg[]>([
    {
      role: 'assistant',
      content:
        'Hi — I’m Dr. Rhidoy. I can walk you through patients, live replay, and beat labels. I don’t diagnose or prescribe.',
    },
  ]);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const hideNudgeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!open) return;
    setNudge(null);
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    inputRef.current?.focus();
  }, [open, messages, busy]);

  useEffect(() => {
    if (open || !nudgeAllowed(pathname)) return;
    const first = setTimeout(() => {
      try { sessionStorage.setItem(NUDGE_KEY, '1'); } catch { /* nudge may repeat; harmless */ }
      setNudge(NUDGE_TEXT);
      hideNudgeTimer.current = setTimeout(() => setNudge(null), 7000);
    }, 9000);
    return () => {
      clearTimeout(first);
      if (hideNudgeTimer.current) clearTimeout(hideNudgeTimer.current);
      setNudge(null);
    };
  }, [open, pathname]);

  useEffect(() => {
    if (!open) return;
    api
      .assistantSuggestions(pathname)
      .then(setSuggestions)
      .catch(() => {
        setSuggestions([
          { id: 'start-monitor', label: 'How do I start live monitoring?' },
          { id: 'beat-labels', label: 'What do the beat labels mean?' },
          { id: 'cannot-do', label: "What can't Dr. Rhidoy do?" },
        ]);
      });
  }, [open, pathname]);

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    setInput('');
    const history = messages.filter((m) => m.content).map((m) => ({ role: m.role, content: m.content }));
    setMessages((prev) => [...prev, { role: 'user', content: message }, { role: 'assistant', content: '' }]);
    setBusy(true);
    try {
      await api.assistantChatStream(
        { message, page: pathname, history },
        {
          onToken: (token) => {
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last?.role === 'assistant') next[next.length - 1] = { ...last, content: last.content + token };
              return next;
            });
          },
          onReplace: (full) => {
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last?.role === 'assistant') next[next.length - 1] = { ...last, content: full };
              return next;
            });
          },
          onDone: (source) => {
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last?.role === 'assistant') {
                next[next.length - 1] = {
                  ...last,
                  content: last.content || 'I couldn’t reach the assistant just now. Try a suggested question.',
                  source,
                };
              }
              return next;
            });
          },
        },
      );
    } catch (err) {
      const detail = err instanceof Error ? err.message : 'Request failed';
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last?.role === 'assistant') next[next.length - 1] = { ...last, content: detail, source: 'error' };
        return next;
      });
    } finally {
      setBusy(false);
    }
  }

  function openChat() {
    setNudge(null);
    setOpen(true);
  }

  return (
    <>
      {!open && nudge && (
        <div className="rhidoy-nudge">
          <button type="button" className="rhidoy-nudge-bubble" onClick={openChat}>
            <span>{nudge}</span>
            <span className="rhidoy-nudge-tail" aria-hidden />
          </button>
          <button type="button" className="rhidoy-nudge-dismiss" aria-label="Dismiss tip" onClick={() => setNudge(null)}>
            ×
          </button>
        </div>
      )}

      <button
        type="button"
        className="rhidoy-fab"
        aria-label={open ? 'Close help' : 'Open help (Dr. Rhidoy)'}
        title="Help"
        onClick={() => (open ? setOpen(false) : openChat())}
      >
        <img src="/brand/dr-rhidoy.svg?v=5" alt="" width={72} height={72} />
      </button>

      <div className={cx('rhidoy-panel', open && 'is-open')} aria-hidden={!open}>
        <header className="rhidoy-panel-head">
          <img src="/brand/dr-rhidoy.svg?v=5" alt="" width={36} height={36} />
          <div className="min-w-0 flex-1">
            <p className="rhidoy-panel-title">Dr. Rhidoy</p>
            <p className="rhidoy-panel-sub">Guide · not a diagnosis</p>
          </div>
          <button type="button" className="rhidoy-panel-close" aria-label="Close chat" onClick={() => setOpen(false)}>
            ×
          </button>
        </header>

        <div className="rhidoy-panel-body">
          <div className="rhidoy-messages">
            {messages.map((m, i) => (
              <div key={`${i}-${m.role}`} className={cx('rhidoy-row', m.role === 'user' ? 'is-user' : 'is-bot')}>
                <div className="rhidoy-bubble">
                  {m.content || (busy && i === messages.length - 1 ? <span className="rhidoy-thinking">Thinking…</span> : null)}
                </div>
              </div>
            ))}
            <div ref={bottomRef} />
          </div>

          <div className={cx('rhidoy-suggestions', suggestionsOpen && 'is-open')}>
            <button
              type="button"
              className="rhidoy-suggestions-toggle"
              aria-expanded={suggestionsOpen}
              aria-controls="rhidoy-suggestion-list"
              onClick={() => setSuggestionsOpen((v) => !v)}
            >
              <span>Suggested</span>
              {suggestions.length > 0 && <span className="rhidoy-suggestions-count">{suggestions.length}</span>}
              <span className="rhidoy-suggestions-chevron" aria-hidden>{suggestionsOpen ? '▾' : '▸'}</span>
            </button>
            <div
              id="rhidoy-suggestion-list"
              className="rhidoy-chips"
              hidden={!suggestionsOpen}
            >
              {suggestions.map((s) => (
                <button key={s.id} type="button" disabled={busy} onClick={() => void send(s.label)}>
                  {s.label}
                </button>
              ))}
            </div>
          </div>

          <form
            className="rhidoy-composer"
            onSubmit={(e) => {
              e.preventDefault();
              void send(input);
            }}
          >
            <input
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask Dr. Rhidoy…"
              disabled={busy}
              aria-label="Message to Dr. Rhidoy"
            />
            <button type="submit" disabled={busy || !input.trim()} aria-label="Send message">
              {busy ? '…' : 'Send'}
            </button>
          </form>
        </div>
      </div>
    </>
  );
}
