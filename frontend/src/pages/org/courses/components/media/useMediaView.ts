import { useCallback, useEffect, useState } from "react";

export type MediaViewMode = "list" | "grid";

/**
 * Stato di presentazione condiviso dei tab media (Video / Video con
 * Avatar): stile di vista (lista compatta vs griglia) e moduli collassati.
 * Entrambi persistiti in localStorage, separati per corso e per variante
 * (così la scelta su "Video" non si trascina su "Video con Avatar").
 *
 * Replica il pattern localStorage delle tab del wizard
 * (`CourseEditorPage`), tollerante agli errori di storage.
 */
export function useMediaView(courseId: string, variant: string) {
  const viewKey = `lesson-media-view:${courseId}:${variant}`;
  const collapsedKey = `lesson-media-collapsed:${courseId}:${variant}`;

  const [viewMode, setViewModeState] = useState<MediaViewMode>(() => {
    try {
      const saved = localStorage.getItem(viewKey);
      if (saved === "list" || saved === "grid") return saved;
    } catch {
      // ignore
    }
    return "grid";
  });

  const setViewMode = useCallback(
    (next: MediaViewMode) => {
      setViewModeState(next);
      try {
        localStorage.setItem(viewKey, next);
      } catch {
        // ignore
      }
    },
    [viewKey],
  );

  const [collapsed, setCollapsed] = useState<Set<string>>(() => {
    try {
      const saved = localStorage.getItem(collapsedKey);
      if (saved) return new Set<string>(JSON.parse(saved));
    } catch {
      // ignore
    }
    return new Set<string>();
  });

  // Persistenza del set moduli chiusi (serializzato come array).
  useEffect(() => {
    try {
      localStorage.setItem(collapsedKey, JSON.stringify([...collapsed]));
    } catch {
      // ignore
    }
  }, [collapsed, collapsedKey]);

  const toggleModule = useCallback((moduleId: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(moduleId)) next.delete(moduleId);
      else next.add(moduleId);
      return next;
    });
  }, []);

  return { viewMode, setViewMode, collapsed, toggleModule };
}
