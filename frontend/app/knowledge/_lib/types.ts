export interface KnowledgeDocument {
  document_id: string;
  document_name: string;
  document_title?: string;
  collection_name: string;
  department_access: string;
  chunk_count: number;
  status: string;
  version: string;
  processing_status?: "uploaded" | "parsing" | "chunking" | "embedding" | "indexing" | "ready" | "failed";
  processing_checkpoint?: "uploaded" | "parsed" | "chunked" | "embedded" | "ready";
  processing_progress?: number;
  error_message?: string | null;
  created_at?: string;
  chunking_config?: ChunkingConfig;
}

export interface KnowledgeChunk {
  id?: string;
  chunk_index: number;
  section_title: string;
  content: string;
  token_count?: number | null;
  page_start?: number | null;
  page_end?: number | null;
  chunking_mode?: ChunkingMode;
  parent_chunk_index?: number | null;
  parent_content?: string | null;
}

export interface RetrievalResult {
  id: string;
  document_id: string;
  document_name: string;
  document_title: string;
  document_type: string;
  version: string;
  section_title: string;
  content: string;
  page?: number | null;
  page_start?: number | null;
  page_end?: number | null;
  score: number;
  citation_tag: string;
  embedding_model: string;
  embedding_version: string;
}

export interface DocumentReader {
  document_id: string;
  document_name: string;
  document_title: string;
  document_type: string;
  version: string;
  content: string;
  character_count: number;
  chunk_count: number;
  processing_status?: KnowledgeDocument["processing_status"];
  processing_checkpoint?: KnowledgeDocument["processing_checkpoint"];
  processing_progress?: number;
  error_message?: string | null;
  chunking_config?: ChunkingConfig | null;
  chunks: KnowledgeChunk[];
  download_url?: string | null;
}

export type ChunkingMode = "standard" | "parent_child";

export interface ChunkingConfig {
  mode: ChunkingMode;
  chunk_size: number;
  chunk_overlap: number;
  parent_chunk_size: number;
}

export interface ChunkPreview {
  document_name: string;
  character_count: number;
  estimated_chunk_count: number;
  chunks: KnowledgeChunk[];
}

export interface Department {
  id: string;
  code: string;
  name: string;
}

export type PipelineStage = "uploading" | "parsing" | "chunking" | "embedding" | "indexing" | "ready" | "failed";

export interface ProcessingStatus {
  document_id: string;
  document_name: string;
  version: string;
  processing_status: PipelineStage | "uploaded";
  processing_checkpoint?: "uploaded" | "parsed" | "chunked" | "embedded" | "ready";
  processing_progress: number;
  chunk_count: number;
  chunk_segments_processed?: number;
  chunk_segments_total?: number;
  chunk_segments_remaining?: number;
  chunks_created?: number;
  embedded_chunks?: number;
  embedding_total_chunks?: number;
  embedding_remaining_chunks?: number;
  embedding_batch_count?: number;
  failed_stage?: Exclude<PipelineStage, "failed"> | null;
  error_message: string | null;
  updated_at?: string | null;
}

export interface AIProcessingProgress {
  processing_stream_id: string;
  event_sequence: number;
  processing_status: "chunking" | "embedding";
  processing_progress: number;
  chunk_segments_processed?: number;
  chunk_segments_total?: number;
  chunk_segments_remaining?: number;
  chunks_created?: number;
  embedded_chunks?: number;
  embedding_total_chunks?: number;
  embedding_remaining_chunks?: number;
  embedding_batch_count?: number;
}
