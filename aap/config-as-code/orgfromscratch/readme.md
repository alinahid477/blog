
***NOTE:*** if you do not want workspace then comment the inventory from confugure_aap. Here I have ccreated a dynamic inventory from red hat insights. 


1. Install the required collection


```
export LC_ALL=C.UTF-8
export LANG=C.UTF-8
cd /workspaces/ansible-lab/configascode
ansible-galaxy collection install -r requirements.yml --force
```

2. Fill in the details in the vars/controller_auth.nogit


```
cp vars/controller_auth.example vars/controller_auth.nogit
aap_hostname: https://<YOUR-AAP-URL>
aap_token: <YOUR-AAP-OAUTH-TOKEN>
github_pat: <YOUR-GITHUB-PAT>
```

3. Run the playbook

```
export ANSIBLE_COLLECTIONS_PATH=/home/dev/.ansible/collections
cd /workspaces/ansible-lab/config-as-code
ansible-playbook -i inventory.yml configure_aap.yml
```
