import DocumentDetailClient from "./DocumentDetailClient";

export default async function DocumentPage({ searchParams }: { searchParams: Promise<{ documentId?: string; version?: string }> }) {
  const params = await searchParams;
  return <DocumentDetailClient documentId={params.documentId || ""} version={params.version || "1.0"} />;
}
