import { Suspense } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { ReviewsTable } from "@/components/reviews/reviews-table";
import { api } from "@/lib/api";
import { toReviewItem } from "@/lib/mappers";

export default async function ReviewsPage() {
  const apiReviews = await api.reviews.listAll();
  const reviews = apiReviews.map(toReviewItem);

  return (
    <div>
      <PageHeader title="Reviews" description="Every human approval, across all projects." />
      <Suspense fallback={null}>
        <ReviewsTable reviews={reviews} />
      </Suspense>
    </div>
  );
}
