// Ports for the mocked-mode preview servers: off Vite's defaults and away from every live port.
export const PREVIEW_PORTS = { store: 4380, operator: 4381, gallery: 4382 } as const;

export const CONTRACT_PORTS = { controller: 18190, control: 18191 } as const;
