"""The recorder's mic notice: input name, live level strip, silent-mic warning.

A long take must never be silently lost to a muted or wrong input, so the
Record card names the active mic, draws a live level strip and, within about
three seconds of no sound, warns. The strip reads the stream the recorder
already holds, so the take opens exactly one ``getUserMedia`` (no second
permission prompt). ``desktop_only`` — WebKit can't fake a media stream.

To watch it fail: delete the ``watchTakeLevel(stream)`` call in ``recorder.js``
— the notice never shows and every assertion below times out.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e._helpers import mock_session_apis, start_recording
from tests.e2e._surface import surface

pytestmark = [pytest.mark.smoke, pytest.mark.desktop_only]

def test_mic_notice_names_input_and_warns_on_silence(
    authed_page: Page, base_url: str
) -> None:
    page = authed_page
    # Count getUserMedia calls and keep the live MediaStream reachable so the
    # test can mute its track (a disabled track delivers exact zeros, which is
    # what a muted or unrouted input looks like to the analyser).
    page.add_init_script(
        """
        (() => {
          const gum = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
          navigator.mediaDevices.getUserMedia = async (c) => {
            window.__gumCalls = (window.__gumCalls || 0) + 1;
            const s = await gum(c);
            window.__vtStream = s;
            return s;
          };
        })();
        """
    )
    mock_session_apis(page, "a long take about nothing in particular")
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    expect(page.locator("#micNotice")).to_be_hidden()

    start_recording(page)

    # Status line: the active input, named by the browser.
    expect(page.locator("#micNotice")).to_be_visible()
    expect(page.locator("#micNotice .mic-notice-input")).to_have_text(re.compile(r"^Mic: .+"))
    # The strip is drawn by the vendored component, inside the notice.
    expect(page.locator("#micNotice .rec-wave")).to_have_count(1)
    expect(page.locator("#micNotice .rec-wave-bars > i")).not_to_have_count(0)
    # One stream: the strip added no getUserMedia call and so no prompt.
    assert page.evaluate("window.__gumCalls") == 1

    # A muted input: no sound for the threshold -> the warning appears.
    page.evaluate("window.__vtStream.getAudioTracks().forEach(t => { t.enabled = false; })")
    expect(page.locator("#micNotice .mic-notice-silent")).to_be_visible(timeout=10_000)
    expect(page.locator("#micNotice .mic-notice-silent")).to_contain_text("No sound from the microphone")
    surface(page, "record.mic-silent")

    # Sound returns -> the warning clears.
    page.evaluate("window.__vtStream.getAudioTracks().forEach(t => { t.enabled = true; })")
    expect(page.locator("#micNotice .mic-notice-silent")).to_be_hidden(timeout=10_000)

    # Stopping the take removes the notice and its audio graph.
    page.click("#recordBtn")
    expect(page.locator("#micNotice")).to_be_hidden()
    expect(page.locator("#micNotice .rec-wave")).to_have_count(0)


def test_silent_remote_audio_input_warns_with_remote_desktop_text(
    authed_page: Page, base_url: str
) -> None:
    """A silent "Remote Audio" input (a Remote Desktop session) gets the
    Remote Desktop wording, not the Bluetooth one."""
    page = authed_page
    # Replace the devices with one silent input named "Remote Audio": an
    # oscillator at zero gain feeds a MediaStream destination, so the analyser
    # reads exact silence the whole take.
    page.add_init_script(
        """
        (() => {
          const md = navigator.mediaDevices;
          const device = {
            deviceId: 'remote-audio', groupId: 'remote', kind: 'audioinput',
            label: 'Remote Audio', toJSON() { return this; },
          };
          md.enumerateDevices = async () => [device];
          md.getUserMedia = async () => {
            const ctx = new AudioContext();
            await ctx.resume();
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            gain.gain.value = 0;
            const dest = ctx.createMediaStreamDestination();
            osc.connect(gain).connect(dest);
            osc.start();
            const stream = dest.stream;
            Object.defineProperty(stream.getAudioTracks()[0], 'label', { value: 'Remote Audio' });
            return stream;
          };
        })();
        """
    )
    mock_session_apis(page, "a take from a remote session")
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    start_recording(page)

    expect(page.locator("#micNotice .mic-notice-input")).to_have_text("Mic: Remote Audio")
    silent = page.locator("#micNotice .mic-notice-silent")
    expect(silent).to_be_visible(timeout=10_000)
    expect(silent).to_contain_text("check the Remote Desktop audio recording setting")
    expect(silent).not_to_contain_text("Bluetooth")
    surface(page, "record.mic-silent-remote")
