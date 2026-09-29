import React, { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

function fmt(ts) {
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export default function Timeline({ onPlaybackChange }) {
  const [timestamps, setTimestamps] = useState([]);
  const [index, setIndex] = useState(null); // null === live
  const pollRef = useRef(null);

  useEffect(() => {
    async function refresh() {
      try {
        const d = await api.getTimeline();
        setTimestamps(d.timestamps_ms);
      } catch (_) {
        /* ignore */
      }
    }
    refresh();
    pollRef.current = setInterval(refresh, 15000);
    return () => clearInterval(pollRef.current);
  }, []);

  async function scrubTo(i) {
    setIndex(i);
    const ts = timestamps[i];
    if (ts == null) return;
    try {
      const snap = await api.getSnapshot(ts);
      onPlaybackChange(snap.ships);
    } catch (_) {
      /* ignore */
    }
  }

  function goLive() {
    setIndex(null);
    onPlaybackChange(null);
  }

  const disabled = timestamps.length < 2;
  const currentLabel = index == null ? "LIVE" : fmt(timestamps[index]);

  return (
    <div className="timeline-bar">
      <button className="btn" onClick={goLive} disabled={index == null}>
        ● Live
      </button>
      <input
        type="range"
        min={0}
        max={Math.max(0, timestamps.length - 1)}
        value={index == null ? Math.max(0, timestamps.length - 1) : index}
        disabled={disabled}
        onChange={(e) => scrubTo(Number(e.target.value))}
      />
      <span className="label">{currentLabel} {timestamps.length > 0 && `· ${timestamps.length} snapshots (last hr)`}</span>
    </div>
  );
}
