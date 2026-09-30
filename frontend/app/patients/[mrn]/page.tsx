import { PatientView } from "@/components/PatientView";

export default async function PatientPage({ params }: { params: Promise<{ mrn: string }> }) {
  const { mrn } = await params;
  return <PatientView mrn={decodeURIComponent(mrn)} />;
}
