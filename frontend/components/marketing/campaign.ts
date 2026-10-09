export const PLATFORMS = ["facebook", "instagram", "threads"] as const;
export type Platform = (typeof PLATFORMS)[number];
export const PLATFORM_LABELS: Record<Platform, string> = {
  facebook: "Facebook",
  instagram: "Instagram",
  threads: "Threads",
};

export type CampaignStage =
  | "OUTLINE_PENDING"
  | "DRAFTING"
  | "DRAFTS_PENDING"
  | "FINAL"
  | "SUBMITTED"
  | "APPROVED"
  | "REJECTED";

export interface Source {
  ref: number;
  chunk_id: string;
  document_id: string;
  document_title: string;
  section_title?: string | null;
  chunk_index?: number | null;
  content: string;
}

export interface FactCheckIssue {
  platform: string;
  kind: "fact" | "cliche";
  claim: string;
  problem: string;
  suggestion: string;
}

export interface FactCheckReport {
  passed: boolean;
  checked?: boolean;
  summary: string;
  issues: FactCheckIssue[];
  round: number;
  edited_platforms?: string[];
}

export interface Campaign {
  id: string;
  title: string;
  stage: CampaignStage;
  brief: string;
  outline: string | null;
  outline_feedback: string | null;
  drafts: Partial<Record<Platform, string>>;
  fact_check_report: FactCheckReport | null;
  refine_rounds: number;
  approval_id: string | null;
  created_by_id: string;
  created_by_name: string | null;
  created_at: string | null;
  updated_at: string | null;
  sources?: Source[];
}

export const STAGE_LABELS: Record<CampaignStage, string> = {
  OUTLINE_PENDING: "Chờ duyệt dàn ý",
  DRAFTING: "Đang viết bài",
  DRAFTS_PENDING: "Chờ duyệt bài viết",
  FINAL: "Đã chốt, chưa gửi duyệt",
  SUBMITTED: "Đang chờ phê duyệt",
  APPROVED: "Đã được duyệt",
  REJECTED: "Bị từ chối, cần sửa",
};

export const STAGE_TONES: Record<CampaignStage, "amber" | "blue" | "violet" | "green" | "red"> = {
  OUTLINE_PENDING: "amber",
  DRAFTING: "blue",
  DRAFTS_PENDING: "amber",
  FINAL: "blue",
  SUBMITTED: "violet",
  APPROVED: "green",
  REJECTED: "red",
};

/** The pipeline stages each step reports, in the order they run. */
export const OUTLINE_STAGES = ["GUARDRAIL", "RAG", "OUTLINE"] as const;
export const DRAFT_STAGES = ["DRAFTS", "FACT_CHECK", "REFINE"] as const;
export const STAGE_NAMES: Record<string, string> = {
  GUARDRAIL: "Kiểm tra an toàn nội dung",
  RAG: "Tra cứu tài liệu công ty",
  OUTLINE: "Soạn dàn ý chiến dịch",
  DRAFTS: "Viết bài 3 kênh",
  FACT_CHECK: "Kiểm chứng số liệu",
  REFINE: "Sửa bài theo kết quả kiểm chứng",
};

export function issuesByPlatform(report: FactCheckReport | null): Record<Platform, number> {
  const counts: Record<Platform, number> = { facebook: 0, instagram: 0, threads: 0 };
  for (const issue of report?.issues ?? []) {
    if (issue.platform in counts) counts[issue.platform as Platform] += 1;
  }
  return counts;
}

/** Threads posts over the 500-character limit, counting the numbered parts. */
export function overlongThreadPosts(text: string | undefined): number {
  return (text ?? "").split(/\n\s*\n/).filter((part) => [...part.trim()].length > 500).length;
}
