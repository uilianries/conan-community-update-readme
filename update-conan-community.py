#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import os
import re
import shutil
import logging
import requests
import tempfile
import subprocess
from contextlib import contextmanager


@contextmanager
def chdir(newdir):
    old_path = os.getcwd()
    os.chdir(newdir)
    try:
        yield
    finally:
        os.chdir(old_path)


def setup_logger():
    logger = logging.getLogger(__file__)
    logger.setLevel(os.getenv("LOGGING_LEVEL", logging.INFO))
    channel = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    channel.setFormatter(formatter)
    logger.addHandler(channel)
    return logger


# Why not PyGithub or any other lib? Because the documentation is a mess!
class Github(object):
    GITHUB_API_URL = "https://api.github.com"

    def __init__(self, logger):
        self.logger = logger
        self.__token = os.getenv("GITHUB_OAUTH_TOKEN", None)
        if not self.__token:
            raise ValueError("'GITHUB_OAUTH_TOKEN' must be configure in your environment")
        self._github_org = os.getenv("GITHUB_ORG", "conan-community")

    def _auth(self):
        return {"Authorization": "token {}".format(self.__token)}

    def get_repositories(self):
        url = f"{Github.GITHUB_API_URL}/orgs/{self._github_org}/repos"
        self.logger.info(f"GET REPOS: {url}")
        response = requests.get(url=url, headers=self._auth())
        if not response.ok:
            raise Exception(response.text)
        return response.json()

    def get_branches(self, full_repo_name):
        url = f"{Github.GITHUB_API_URL}/repos/{full_repo_name}/branches"
        response = requests.get(url=url, headers=self._auth())
        if not response.ok:
            raise Exception(response.text)
        branches = []
        for branch in response.json():
            branches.append(branch["name"])
        return branches

    def create_pull_request(self, full_repo_name, head, base):
        url = f"{Github.GITHUB_API_URL}/repos/{full_repo_name}/pulls"
        headers = self._auth()
        headers["Content-Type"] = "application/json"
        data = {"title": "Apply README Generator",
                "body": "This PR was created by a bot. We have used conan-readme-generator for this PR.",
                "head": head,
                "base": base}
        # requests only returns 404 ¯\_(ツ)_/¯
        output = subprocess.check_output(["curl", "-s", "-X", "POST", "-H", "Content-Type: Application/json", "-H", f"Authorization: token {self.__token}", "-d", json.dumps(data), url]).decode()
        json_data = json.loads(output)
        if "errors" in json_data.keys():
            if "already exists" in json_data["errors"][0]["message"] or \
                "No commits between" in json_data["errors"][0]["message"]:
                self.logger.warning(json_data["errors"][0]["message"])
            else:
                raise Exception(json_data["errors"][0]["message"])
        elif "html_url" in json_data:
            self.logger.info(json_data["html_url"])
        else:
            raise Exception(json_data["message"])

    def clone_repo(self, full_repo_name):
        tempdir_path = tempfile.mkdtemp()
        subprocess.check_call(["git", "clone", f"https://{self.__token}:@github.com/{full_repo_name}.git", tempdir_path])
        return tempdir_path


class ReadmeGenerator(object):

    def __init__(self, logger, github):
        self.logger = logger
        self.github = github
        self._readme_template = os.path.join(os.getcwd(), "templates", "readme", "README-library.md.tmpl")
        self._license_template = os.path.join(os.getcwd(), "templates", "license", "LICENSE-mit.md.tmpl")

    def run_readme_generator(self, full_repo_name, repo_path, branches):
        with chdir(repo_path):
            pair_branches = self._get_pair_branches(branches)
            self._update_origin(full_repo_name)
            for stable, testing in pair_branches.items():
                self._checkout(testing)
                self._apply_templates()
                if self._commit():
                    self._push(testing)
                    self.github.create_pull_request(full_repo_name=full_repo_name, base=stable, head=testing)

    def _checkout(self, branch):
        self.logger.info(f"Checkout to branch {branch}")
        subprocess.check_call(["git", "checkout", branch])

    def _update_origin(self, full_repo_name):
        subprocess.check_call(["git", "remote", "set-url", "origin", f"git@github.com:{full_repo_name}.git"])

    def _commit(self):
        try:
            output = subprocess.check_output(["git", "commit", "-a", "-s", "-m", "Apply Conan Readme Generator [skip ci]"]).decode()
            if "Apply Conan Readme Generator" not in output:
                raise Exception("Could not commit changes")
            return False
        except subprocess.CalledProcessError as error:
            if "Your branch is up to date" in str(error.output):
                return False
            elif "nothing to commit, working tree clean" not in str(error.output):
                raise Exception(f"Could not commit changes: {error.output}")


    def _push(self, branch):
        try:
            subprocess.check_output(["git", "push", "origin", branch]).decode()
        except subprocess.CalledProcessError as error:
            if "Everything up-to-date" not in str(error.output):
                raise Exception(f"Could not push changes: {error.output}")

    def _apply_templates(self):
        try:
            subprocess.check_call(["conan-readme-generator", "conan/stable", self._readme_template, self._license_template])
            if os.path.isfile("LICENSE"):
                shutil.move("LICENSE.md", "LICENSE")
        except subprocess.CalledProcessError as error:
            self.logger.error(str(error.output))

    def _get_pair_branches(self, branches):
        pattern = re.compile(r"testing\/(.*)")
        pair_branches = {}
        testing_branches = [branch for branch in branches if "testing" in branch]
        stable_branches = [branch for branch in branches if "release" in branch or "stable" in branch]
        for branch in testing_branches:
            match = pattern.match(branch)
            if not match:
                continue
            version = match.group(1)
            for name in ["release", "stable"]:
                if f"{name}/{version}" in stable_branches:
                    pair_branches[f"{name}/{version}"] = branch
        return pair_branches


def main():
    logger = setup_logger()
    github = Github(logger)
    generator = ReadmeGenerator(logger, github)
    repos = github.get_repositories()
    for repo in repos:
        if not repo["name"].startswith("conan-"):
            continue
        full_name = repo["full_name"]
        logger.info(f"Cloning {full_name} ...")
        repo_path = github.clone_repo(full_name)
        branches = github.get_branches(full_name)
        generator.run_readme_generator(full_name, repo_path, branches)


if __name__ == "__main__":
    main()
