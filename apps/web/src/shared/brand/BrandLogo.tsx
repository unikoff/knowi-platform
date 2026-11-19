import darkLogo from "./knowi-logo-horizontal-01-dark.svg";
import lightLogo from "./knowi-logo-horizontal-01-light.svg";
import "./brand-logo.css";

export function BrandLogo() {
  return <span className="brand-logo" role="img" aria-label="knowi">
    <img className="brand-logo__dark" src={darkLogo} alt="" />
    <img className="brand-logo__light" src={lightLogo} alt="" />
  </span>;
}
