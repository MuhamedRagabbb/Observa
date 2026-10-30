export class PendingWork {
  private readonly pending = new Set<Promise<void>>();

  track<T>(work: Promise<T>): Promise<T> {
    const settled = work.then(
      () => { this.pending.delete(settled); },
      () => { this.pending.delete(settled); },
    );
    this.pending.add(settled);
    return work;
  }

  get size(): number {
    return this.pending.size;
  }

  async drain(timeoutMs: number): Promise<{ timedOut: boolean }> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<"timeout">((resolve) => {
      timer = setTimeout(() => resolve("timeout"), timeoutMs);
    });
    try {
      while (this.pending.size > 0) {
        const result = await Promise.race([Promise.all(this.pending), timeout]);
        if (result === "timeout") return { timedOut: this.pending.size > 0 };
      }
      return { timedOut: false };
    } finally {
      clearTimeout(timer);
    }
  }
}
