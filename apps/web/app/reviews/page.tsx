import { Suspense } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { ReviewsTable } from "@/components/reviews/reviews-table";
import { mockReviews } from "@/lib/mock-data";

export default function ReviewsPage() {
  return (
    <div>
      <PageHeader title="Reviews" description="Every human approval, across all projects." />
      <Suspense fallback={null}>
        <ReviewsTable reviews={mockReviews} />
      </Suspense>
    </div>
  );
}
