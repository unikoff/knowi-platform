# Landing route

`CourseLandingRoute.tsx` loads the `Page` export of the registered course's declared personal/about entrypoint and supplies `courseId` and `legacy`. The course package owns its content, forms, assets and page styles.

The entrypoints are declared in `course.yaml` under `frontend.personal_landing` and `frontend.course_landing`. See `docs/course-isolation/landing.md` for the migration handoff and checks.
