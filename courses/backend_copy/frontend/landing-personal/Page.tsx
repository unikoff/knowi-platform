import { useEffect } from "react";
import { courseUrls } from "@platform/shared/course-paths";
import { landingUrl as legacyLandingUrl, platformUrl as legacyLearningUrl } from "@platform/shared/paths";
import { BackendLandingPage } from "./BackendLandingPage";
import { LandingDetailPage } from "./LandingPages";
import { PersonalDataPage } from "./PersonalDataPage";
import { initLandingAnalytics } from "./analytics";

type PageProps = { courseId: string; legacy?: boolean };

export function Page({ courseId, legacy = false }: PageProps) {
  useEffect(() => { initLandingAnalytics(); }, []);
  const page = window.location.pathname.replace(/\/+$/, "").split("/").pop();
  const urls = legacy && courseId === "backend"
    ? { personal: legacyLandingUrl, learning: legacyLearningUrl }
    : courseUrls(import.meta.env.BASE_URL, courseId);
  const routeProps = { courseId, landingBaseUrl: urls.personal, learningUrl: urls.learning };

  if (page === "personal-data") return <PersonalDataPage key={courseId} {...routeProps} />;
  if (page === "privacy-policy") return <PersonalDataPage key={courseId} {...routeProps} policy />;
  if (page === "reviews" || page === "platform" || page === "pricing") {
    return <LandingDetailPage key={courseId} {...routeProps} page={page} />;
  }
  return <BackendLandingPage key={courseId} {...routeProps} />;
}
