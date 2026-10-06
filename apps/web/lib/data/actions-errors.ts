export class ConflictError extends Error {
  constructor() {
    super("This case was changed by someone else. Reload to see the latest version before saving again.");
  }
}

export class GateError extends Error {}
