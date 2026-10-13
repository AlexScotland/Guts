"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message } from "../lib/types";
import styles from "./MessageList.module.css";

function formatTime(ts: number): string {
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function MessageBubble({ message }: { message: Message }) {
  const [copied, setCopied] = useState(false);
  const isUser = message.role === "user";

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard unavailable */
    }
  };

  const isCode = (message.content || "").trim().startsWith("```");

  return (
    <div className={`${styles.row} ${isUser ? styles.rowUser : styles.rowAgent}`}>
      {!isUser && (
        <div className={styles.badge} aria-hidden="true">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none">
            <path
              d="M12 3.2l1.6 4.2 4.2 1.6-4.2 1.6L12 14.8l-1.6-4.2L6.2 9l4.2-1.6L12 3.2z"
              fill="currentColor"
            />
          </svg>
        </div>
      )}

      <div
        className={`${styles.bubble} ${isUser ? styles.bubbleUser : styles.bubbleAgent} ${
          message.error ? styles.bubbleError : ""
        }`}
      >
        {isCode ? (
          <pre className={styles.code}>
            <code>{message.content.replace(/^```|```$/g, "").trim()}</code>
          </pre>
        ) : (
          <div className={styles.md}>
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                a: ({ href, children }) => (
                  <a href={href} target="_blank" rel="noreferrer">
                    {children}
                  </a>
                ),
              }}
            >
              {message.content}
            </ReactMarkdown>
          </div>
        )}

        <div className={styles.meta}>
          <span>{formatTime(message.timestamp)}</span>
          {copied ? (
            <span className={styles.copied}>Copied</span>
          ) : (
            <button className={styles.copy} onClick={copy} title="Copy message">
              Copy
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function TypingDots() {
  return (
    <div className={`${styles.row} ${styles.rowAgent}`} aria-label="Agent is working">
      <div className={styles.badge} aria-hidden="true">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none">
          <path
            d="M12 3.2l1.6 4.2 4.2 1.6-4.2 1.6L12 14.8l-1.6-4.2L6.2 9l4.2-1.6L12 3.2z"
            fill="currentColor"
          />
        </svg>
      </div>
      <div className={`${styles.bubble} ${styles.bubbleAgent}`}>
        <span className={styles.dots}>
          <i />
          <i />
          <i />
        </span>
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className={styles.empty}>
      <div className={styles.emptyMark} aria-hidden="true">
        <svg viewBox="0 0 24 24" width="30" height="30" fill="none">
          <path
            d="M12 2.5l2 5.2 5.2 2-5.2 2-2 5.2-2-5.2-5.2-2 5.2-2 2-5.2z"
            fill="var(--accent)"
          />
        </svg>
      </div>
      <h2 className={styles.emptyTitle}>Ask your agent anything</h2>
      <p className={styles.emptyText}>
        It reasons, uses tools, searches, reads your PDFs and code, and keeps
        long-term memory. Every reply is grounded in what it actually looks up.
      </p>
      <div className={styles.hints}>
        <span className={styles.hint}>
          <kbd>Enter</kbd> to send
        </span>
        <span className={styles.hint}>
          <kbd>Shift</kbd>+<kbd>Enter</kbd> newline
        </span>
        <span className={styles.hint}>Thread saved locally</span>
      </div>
    </div>
  );
}

export default function MessageList({
  messages,
  loading,
}: {
  messages: Message[];
  loading: boolean;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(true);

  // Auto-scroll only while the user is near the bottom — don't yank them up
  // if they scrolled back to re-read earlier turns.
  useEffect(() => {
    if (pinned) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading, pinned]);

  return (
    <div
      className={styles.wrap}
      role="log"
      aria-live="polite"
      onScroll={(e) => {
        const el = e.currentTarget;
        const nearBottom =
          el.scrollHeight - el.scrollTop - el.clientHeight < 140;
        setPinned(nearBottom);
      }}
    >
      {messages.length === 0 && !loading ? (
        <EmptyState />
      ) : (
        <>
          {messages.map((m, i) => (
            <MessageBubble key={i} message={m} />
          ))}
          {loading && <TypingDots />}
        </>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
