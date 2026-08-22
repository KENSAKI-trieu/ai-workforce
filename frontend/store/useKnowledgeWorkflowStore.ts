import { create } from "zustand";

import type { ChunkPreview, ChunkingConfig } from "@/app/knowledge/_lib/types";

interface KnowledgeWorkflowState {
  file: File | null;
  collection: string;
  department: string;
  config: ChunkingConfig;
  preview: ChunkPreview | null;
  documentId: string | null;
  version: string;
  setSource: (file: File, collection: string, department: string) => void;
  setConfig: (config: ChunkingConfig) => void;
  setPreview: (preview: ChunkPreview | null) => void;
  setUploadedDocument: (documentId: string, version: string) => void;
  reset: () => void;
}

const defaultConfig: ChunkingConfig = {
  mode: "standard",
  chunk_size: 700,
  chunk_overlap: 80,
  parent_chunk_size: 1024,
};

export const useKnowledgeWorkflowStore = create<KnowledgeWorkflowState>((set) => ({
  file: null,
  collection: "General Knowledge",
  department: "ALL",
  config: defaultConfig,
  preview: null,
  documentId: null,
  version: "1.0",
  setSource: (file, collection, department) => set({
    file,
    collection,
    department,
    preview: null,
    documentId: null,
    version: "1.0",
  }),
  setConfig: (config) => set({ config, preview: null }),
  setPreview: (preview) => set({ preview }),
  setUploadedDocument: (documentId, version) => set({ documentId, version }),
  reset: () => set({
    file: null,
    collection: "General Knowledge",
    department: "ALL",
    config: defaultConfig,
    preview: null,
    documentId: null,
    version: "1.0",
  }),
}));
