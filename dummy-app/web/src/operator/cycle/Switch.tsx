import styles from "./Cycle.module.css";

export function Switch({ checked, locked, onChange }: { checked: boolean; locked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <button className={styles.switch} type="button" role="switch" aria-checked={checked} aria-disabled={locked || undefined}
      onClick={() => { if (!locked) onChange(!checked); }}>
      <span className={styles.switchTrack} aria-hidden="true"><span /></span>
      <span>Shuffle scenarios</span>
    </button>
  );
}
