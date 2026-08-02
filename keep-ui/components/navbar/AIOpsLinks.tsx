"use client";

import { Subtitle } from "@tremor/react";
import { LinkWithIcon } from "components/LinkWithIcon";
import { Disclosure } from "@headlessui/react";
import { IoChevronUp } from "react-icons/io5";
import { RiRobot2Line, RiShieldKeyholeLine } from "react-icons/ri";
import {
  MdOutlineSpaceDashboard,
  MdOutlineHandyman,
  MdOutlineSettings,
} from "react-icons/md";
import clsx from "clsx";
import { useAiopsStats, useAiopsTools } from "@/entities/aiops/model/useAiops";

/**
 * AIOps control-plane section.
 *
 * The count badge is the number of investigations that are still running —
 * that is the only number an operator acts on from the sidebar. When the
 * aiops-api is unreachable the hook errors and the badge is simply absent;
 * the links still work and each page renders its own degraded state.
 */
export const AIOpsLinks = () => {
  const { stats } = useAiopsStats();
  const { catalog } = useAiopsTools();

  // Tools still returning demo data — worth a nudge from the sidebar,
  // since evidence from them cannot support a real conclusion.
  const stubbedCount = catalog
    ? catalog.tools.filter((tool) => tool.mode !== "live").length
    : undefined;

  const inFlight = stats
    ? (stats.investigations_by_status.queued ?? 0) +
      (stats.investigations_by_status.gathering ?? 0) +
      (stats.investigations_by_status.hypothesizing ?? 0)
    : undefined;

  return (
    <Disclosure as="div" className="space-y-0.5" defaultOpen>
      <Disclosure.Button className="w-full flex justify-between items-center px-2">
        {({ open }) => (
          <>
            <Subtitle className="text-xs ml-2 text-gray-900 font-medium uppercase">
              AIOPS
            </Subtitle>
            <IoChevronUp
              className={clsx({ "rotate-180": open }, "mr-2 text-slate-400")}
            />
          </>
        )}
      </Disclosure.Button>

      <Disclosure.Panel as="ul" className="space-y-0.5 p-1 pr-1">
        <li>
          <LinkWithIcon
            href="/aiops"
            icon={MdOutlineSpaceDashboard}
            testId="aiops-overview"
          >
            <Subtitle className="text-xs">Overview</Subtitle>
          </LinkWithIcon>
        </li>
        <li>
          <LinkWithIcon
            href="/aiops/investigations"
            icon={RiRobot2Line}
            count={inFlight || undefined}
            testId="aiops-investigations"
          >
            <Subtitle className="text-xs">Investigations</Subtitle>
          </LinkWithIcon>
        </li>
        <li>
          <LinkWithIcon
            href="/aiops/tools"
            icon={MdOutlineHandyman}
            count={stubbedCount || undefined}
            testId="aiops-tools"
          >
            <Subtitle className="text-xs">Tools</Subtitle>
          </LinkWithIcon>
        </li>
        <li>
          <LinkWithIcon
            href="/settings?selectedTab=ai-policies"
            icon={RiShieldKeyholeLine}
            testId="aiops-policies"
          >
            <Subtitle className="text-xs">Policies</Subtitle>
          </LinkWithIcon>
        </li>
        <li>
          <LinkWithIcon
            href="/settings?selectedTab=ai-agents"
            icon={MdOutlineSettings}
            testId="aiops-settings"
          >
            <Subtitle className="text-xs">Agent Settings</Subtitle>
          </LinkWithIcon>
        </li>
      </Disclosure.Panel>
    </Disclosure>
  );
};
