"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import styles from "./ChatInput.module.css";

interface ChatInputProps {
  onSend: (text: string) => void;
  loading: boolean;
  onStop: () => void;
}

export default function ChatInput({ onSend, loading, onStop }: ChatInputProps) {
  const [value, setValue] = useState("");
  const taRef = useRef<HTMLTextAreaElement>(null);
  const [focused, setFocused] = useState(false);

  // Auto-grow the textarea with its content (capped).
  const resize = useCallback(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
  }, []);

  useEffect(() => {
    resize();
  }, [value, resize]);

  // Focus the composer when idle and the user hasn't just cleared focus.
  useEffect(() => {
    if (!loading && taRef.current && document.hasFocus()) {
      taRef.current.focus();
    }
  }, [loading]);

  const trimmed = value.trim();
  const canSend = trimmed.length > 0 && !loading;

  const submit = useCallback(() => {
    if (!canSend) return;
    onSend(trimmed);
    setValue("");
    requestAnimationFrame(() => {
      if (taRef.current) taRef.current.style.height = "auto";
      taRef.current?.focus();
    });
  }, [canSend, onSend, trimmed]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className={styles.dockWrap}>
      <div className={`${styles.dock} ${focused ? styles.dockFocused : ""}`}>
        <textarea
          ref={taRef}
          className={styles.textarea}
          rows={1}
          value={value}
          placeholder={
            loading ? "Agent is working…" : "Message the agent…"
          }
          aria-label="Message"
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          disabled={false}
        />

        {loading ? (
          <button
            className={styles.stopBtn}
            onClick={onStop}
            title="Stop generating"
            aria-label="Stop generating"
          >
            <span className={styles.stopSquare} />
          </button>
        ) : (
          <button
            className={styles.sendBtn}
            onClick={submit}
            disabled={!canSend}
            title={canSend ? "Send (Enter)" : "Type a message"}
            aria-label="Send message"
          >
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" aria-hidden="true">
              <path
                d="M4.5 12L19 5l-4.2 14-3.3-5.2L4.5 12z"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinejoin="round"
                strokeLinecap="round"
              />
            </svg>
          </button>
        )}
      </div>
      <p className={styles.hint}>
        <kbd>Enter</kbd> send · <kbd>Shift</kbd>+<kbd>Enter</kbd> newline
      </p>
    </div>
  );
}
